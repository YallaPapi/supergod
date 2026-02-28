"""Scheduler — tracks workers, assigns subtasks, handles failures."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from websockets.asyncio.server import ServerConnection

from supergod.shared.protocol import (
    TaskStatus,
    WorkerStatus,
    WorkerTaskMsg,
    serialize,
)
from supergod.orchestrator.state import StateDB

log = logging.getLogger(__name__)


@dataclass
class WorkerConnection:
    name: str
    ws: ServerConnection
    status: WorkerStatus = WorkerStatus.IDLE


class Scheduler:
    def __init__(self, db: StateDB):
        self.db = db
        self.workers: dict[str, WorkerConnection] = {}
        self._assignment_lock = asyncio.Lock()

    async def register_worker(self, name: str, ws: ServerConnection) -> None:
        self.workers[name] = WorkerConnection(name=name, ws=ws)
        await self.db.upsert_worker(name, WorkerStatus.IDLE)
        log.info("Worker %s registered (%d total)", name, len(self.workers))

    async def unregister_worker(self, name: str) -> None:
        self.workers.pop(name, None)
        await self.db.upsert_worker(name, WorkerStatus.OFFLINE)
        log.info("Worker %s unregistered (%d remaining)", name, len(self.workers))

    def get_idle_workers(self) -> list[WorkerConnection]:
        return [w for w in self.workers.values() if w.status == WorkerStatus.IDLE]

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
            await self.db.set_worker_task(worker.name, subtask_id)
            await self.db.update_subtask(
                subtask_id,
                status=TaskStatus.RUNNING,
                worker_name=worker.name,
            )

            msg = WorkerTaskMsg(
                id=subtask_id,
                prompt=subtask["prompt"],
                branch=subtask["branch"],
            )
            await worker.ws.send(serialize(msg))
            log.info(
                "Assigned subtask %s to %s", subtask_id, worker.name
            )
            return worker.name

    async def handle_task_complete(
        self, worker_name: str, subtask_id: str, commit: str
    ) -> None:
        if worker_name in self.workers:
            self.workers[worker_name].status = WorkerStatus.IDLE
        await self.db.set_worker_task(worker_name, None)
        await self.db.update_subtask(
            subtask_id, status=TaskStatus.COMPLETED, commit_sha=commit
        )
        log.info("Subtask %s completed by %s (commit: %s)", subtask_id, worker_name, commit)

    async def handle_task_error(
        self, worker_name: str, subtask_id: str, error: str
    ) -> None:
        if worker_name in self.workers:
            self.workers[worker_name].status = WorkerStatus.IDLE
        await self.db.set_worker_task(worker_name, None)
        await self.db.update_subtask(subtask_id, status=TaskStatus.FAILED)
        log.error("Subtask %s failed on %s: %s", subtask_id, worker_name, error)

    async def try_assign_ready_subtasks(self, task_id: str) -> int:
        """Try to assign all ready subtasks for a task. Returns count assigned."""
        ready = await self.db.get_ready_subtasks(task_id)
        assigned = 0
        for subtask in ready:
            worker_name = await self.assign_subtask(subtask, task_id)
            if worker_name:
                assigned += 1
            else:
                break  # No more idle workers
        return assigned

    async def all_subtasks_done(self, task_id: str) -> bool:
        subtasks = await self.db.get_subtasks_for_task(task_id)
        return all(
            s["status"] in (TaskStatus.COMPLETED, TaskStatus.FAILED)
            for s in subtasks
        )

    async def any_subtask_failed(self, task_id: str) -> bool:
        subtasks = await self.db.get_subtasks_for_task(task_id)
        return any(s["status"] == TaskStatus.FAILED for s in subtasks)
