"""Scheduler -- tracks workers, assigns subtasks, handles failures."""

import asyncio
import logging
from dataclasses import dataclass, field

from fastapi import WebSocket

from supergod.shared.protocol import (
    PingMsg,
    TaskStatus,
    WorkerStatus,
    WorkerTaskMsg,
    new_id,
    serialize,
)
from supergod.shared.config import (
    MAX_WORKERS_PER_TASK,
    SERVER_PING_INTERVAL,
    SERVER_PING_TIMEOUT,
    SUBTASK_MAX_RETRIES,
)
from supergod.orchestrator.state import StateDB

log = logging.getLogger(__name__)


@dataclass
class WorkerConnection:
    name: str
    ws: WebSocket
    status: WorkerStatus = WorkerStatus.IDLE
    last_pong: float = 0.0  # monotonic timestamp of last pong received


class Scheduler:
    def __init__(self, db: StateDB):
        self.db = db
        self.workers: dict[str, WorkerConnection] = {}
        self._assignment_lock = asyncio.Lock()
        self._ping_task: asyncio.Task | None = None

    async def start_ping_loop(self) -> None:
        """Start the periodic ping loop. Call once at server startup."""
        if self._ping_task is None:
            self._ping_task = asyncio.create_task(self._ping_loop())

    async def stop_ping_loop(self) -> None:
        """Stop the periodic ping loop. Call at server shutdown."""
        if self._ping_task:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
            self._ping_task = None

    async def _ping_loop(self) -> None:
        """Periodically ping all workers and disconnect those that don't respond."""
        loop = asyncio.get_event_loop()
        while True:
            await asyncio.sleep(SERVER_PING_INTERVAL)
            now = loop.time()
            dead_workers = []
            ping_msg = serialize(PingMsg())

            for name, wc in list(self.workers.items()):
                # Check if worker missed the pong deadline
                if wc.last_pong > 0 and (now - wc.last_pong) > (SERVER_PING_INTERVAL + SERVER_PING_TIMEOUT):
                    log.warning("Worker %s missed pong deadline, marking dead", name)
                    dead_workers.append(name)
                    continue
                # Send ping
                try:
                    await wc.ws.send_text(ping_msg)
                except Exception:
                    log.warning("Failed to ping worker %s, marking dead", name)
                    dead_workers.append(name)

            for name in dead_workers:
                await self.unregister_worker(name)

    async def register_worker(self, name: str, ws: WebSocket) -> None:
        loop = asyncio.get_event_loop()
        self.workers[name] = WorkerConnection(name=name, ws=ws, last_pong=loop.time())
        await self.db.upsert_worker(name, WorkerStatus.IDLE)
        log.info("Worker %s registered (%d total)", name, len(self.workers))

    async def unregister_worker(self, name: str) -> None:
        wc = self.workers.pop(name, None)
        worker_row = await self.db.get_worker(name)
        current_subtask = (
            worker_row.get("current_subtask") if worker_row else None
        )
        if current_subtask:
            log.warning(
                "Worker %s disconnected while running %s; reclaiming subtask",
                name,
                current_subtask,
            )
            await self.handle_task_error(
                name, current_subtask, "Worker disconnected"
            )
            subtask = await self.db.get_subtask(current_subtask)
            if subtask:
                await self.try_assign_ready_subtasks(subtask["task_id"])
        await self.db.upsert_worker(name, WorkerStatus.OFFLINE)
        # Try to close the websocket gracefully
        if wc:
            try:
                await wc.ws.close()
            except Exception:
                pass
        log.info("Worker %s unregistered (%d remaining)", name, len(self.workers))

    def record_pong(self, worker_name: str) -> None:
        """Record that a worker responded to a ping."""
        wc = self.workers.get(worker_name)
        if wc:
            loop = asyncio.get_event_loop()
            wc.last_pong = loop.time()

    def get_idle_workers(self) -> list[WorkerConnection]:
        return [w for w in self.workers.values() if w.status == WorkerStatus.IDLE]

    def get_worker_name_by_ws(self, ws: WebSocket) -> str | None:
        """Look up worker name by WebSocket reference."""
        for name, wc in self.workers.items():
            if wc.ws is ws:
                return name
        return None

    async def assign_subtask(
        self, subtask: dict, task_id: str
    ) -> str | None:
        """Assign a subtask to an idle worker. Returns worker name or None."""
        async with self._assignment_lock:
            idle = self.get_idle_workers()
            if not idle:
                return None

            worker = idle[0]
            worker.status = WorkerStatus.BUSY

            subtask_id = subtask["subtask_id"]
            execution_token = new_id()
            await self.db.set_worker_task(worker.name, subtask_id)
            claimed = await self.db.claim_subtask_execution(
                subtask_id, worker.name, execution_token
            )
            if not claimed:
                worker.status = WorkerStatus.IDLE
                await self.db.set_worker_task(worker.name, None)
                return None
            await self.db.update_subtask(
                subtask_id, error_message="", failure_category=""
            )
            await self.db.update_task_status(task_id, TaskStatus.RUNNING)

            msg = WorkerTaskMsg(
                id=subtask_id,
                prompt=subtask["prompt"],
                branch=subtask["branch"],
                execution_token=execution_token,
            )
            try:
                await worker.ws.send_text(serialize(msg))
            except Exception as e:
                log.error("Failed to send task to worker %s: %s", worker.name, e)
                # Revert assignment
                worker.status = WorkerStatus.IDLE
                await self.db.set_worker_task(worker.name, None)
                await self.db.release_subtask_execution(
                    subtask_id, execution_token, next_status=TaskStatus.PENDING
                )
                return None

            log.info(
                "Assigned subtask %s to %s", subtask_id, worker.name
            )
            return worker.name

    async def handle_task_complete(
        self,
        worker_name: str,
        subtask_id: str,
        commit: str,
        execution_token: str = "",
    ) -> None:
        subtask = await self.db.get_subtask(subtask_id)
        if subtask and subtask["status"] in (
            TaskStatus.CANCELLED,
            TaskStatus.BLOCKED,
        ):
            log.info(
                "Ignoring completion for terminal subtask %s (%s)",
                subtask_id,
                subtask["status"],
            )
            return
        if subtask and not _execution_matches(
            subtask, worker_name, execution_token
        ):
            log.warning(
                "Ignoring stale completion for %s from %s (token=%s, current=%s)",
                subtask_id,
                worker_name,
                execution_token or "<none>",
                subtask.get("execution_token", ""),
            )
            return
        if worker_name in self.workers:
            self.workers[worker_name].status = WorkerStatus.IDLE
        await self.db.set_worker_task(worker_name, None)
        await self.db.update_subtask(
            subtask_id,
            status=TaskStatus.COMPLETED,
            commit_sha=commit,
            error_message="",
            failure_category="",
            execution_token="",
        )
        log.info("Subtask %s completed by %s (commit: %s)", subtask_id, worker_name, commit)

    async def handle_task_error(
        self,
        worker_name: str,
        subtask_id: str,
        error: str,
        execution_token: str = "",
    ) -> None:
        subtask = await self.db.get_subtask(subtask_id)
        if not subtask:
            log.warning("Error for unknown subtask %s: %s", subtask_id, error)
            return

        if worker_name in self.workers:
            self.workers[worker_name].status = WorkerStatus.IDLE
        await self.db.set_worker_task(worker_name, None)

        if subtask["status"] in (
            TaskStatus.CANCELLED,
            TaskStatus.BLOCKED,
            TaskStatus.COMPLETED,
        ):
            log.info(
                "Ignoring error for terminal subtask %s (%s): %s",
                subtask_id,
                subtask["status"],
                error,
            )
            return
        if not _execution_matches(subtask, worker_name, execution_token):
            log.warning(
                "Ignoring stale error for %s from %s (token=%s, current=%s): %s",
                subtask_id,
                worker_name,
                execution_token or "<none>",
                subtask.get("execution_token", ""),
                error,
            )
            return

        category = _categorize_error(error)
        attempts = int(subtask.get("attempt_count") or 0)
        can_retry = _is_retryable(category) and attempts < SUBTASK_MAX_RETRIES

        if can_retry:
            await self.db.update_subtask(
                subtask_id,
                status=TaskStatus.PENDING,
                worker_name=None,
                attempt_count=attempts + 1,
                error_message=error,
                failure_category=category,
                execution_token="",
            )
            log.warning(
                "Subtask %s returned to pending (attempt %d/%d, category=%s): %s",
                subtask_id,
                attempts + 1,
                SUBTASK_MAX_RETRIES,
                category,
                error,
            )
            return

        final_status = (
            TaskStatus.CANCELLED if category == "cancelled" else TaskStatus.FAILED
        )
        await self.db.update_subtask(
            subtask_id,
            status=final_status,
            worker_name=None,
            attempt_count=attempts + 1,
            error_message=error,
            failure_category=category,
            execution_token="",
        )
        if final_status == TaskStatus.FAILED:
            blocked = await self.db.cascade_failure(
                subtask["task_id"], subtask_id
            )
            if blocked:
                log.warning(
                    "Cascade blocked %d subtasks after %s failed",
                    len(blocked),
                    subtask_id,
                )
        log.error(
            "Subtask %s failed on %s (category=%s): %s",
            subtask_id,
            worker_name,
            category,
            error,
        )

    async def try_assign_ready_subtasks(self, task_id: str) -> int:
        """Try to assign all ready subtasks for a task. Returns count assigned."""
        return await self.try_assign_ready_subtasks_with_limit(task_id, None)

    async def try_assign_ready_subtasks_with_limit(
        self, task_id: str, max_assign: int | None
    ) -> int:
        """Assign ready subtasks with optional per-call assignment cap."""
        task = await self.db.get_task(task_id)
        if not task:
            return 0
        if task["status"] in (
            TaskStatus.CANCELLED,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.PAUSED,
        ):
            return 0

        ready = await self.db.get_ready_subtasks(task_id)
        assigned = 0
        for subtask in ready:
            if max_assign is not None and assigned >= max_assign:
                break
            if MAX_WORKERS_PER_TASK > 0:
                running = await self.db.count_running_subtasks(task_id)
                if running >= MAX_WORKERS_PER_TASK:
                    break
            worker_name = await self.assign_subtask(subtask, task_id)
            if worker_name:
                assigned += 1
            else:
                break  # No more idle workers
        return assigned

    async def all_subtasks_done(self, task_id: str) -> bool:
        return await self.db.all_terminal(task_id)

    async def any_subtask_failed(self, task_id: str) -> bool:
        subtasks = await self.db.get_subtasks_for_task(task_id)
        return any(
            s["status"] in (TaskStatus.FAILED, TaskStatus.BLOCKED)
            for s in subtasks
        )


def _categorize_error(error: str) -> str:
    e = (error or "").lower()
    if "cancel" in e:
        return "cancelled"
    if "busy" in e:
        return "transient_busy"
    if any(x in e for x in ("disconnect", "connection", "timed out", "timeout")):
        return "transient_infra"
    if any(x in e for x in ("auth", "login", "permission denied", "unauthorized")):
        return "auth"
    if "merge conflict" in e:
        return "merge_conflict"
    if any(x in e for x in ("test failed", "pytest", "assertionerror")):
        return "test_failure"
    return "execution_error"


def _is_retryable(category: str) -> bool:
    return category in {"transient_busy", "transient_infra"}


def _execution_matches(
    subtask: dict, worker_name: str, execution_token: str
) -> bool:
    current_worker = subtask.get("worker_name")
    current_token = subtask.get("execution_token", "")
    if execution_token:
        return current_token == execution_token
    return current_worker == worker_name
