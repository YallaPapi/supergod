"""Worker daemon -- connects to orchestrator, executes tasks via Codex CLI."""

import asyncio
import logging
import os
import random
import signal
import sys
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import websockets
from websockets.exceptions import ConnectionClosed

from supergod.shared.config import (
    ORCHESTRATOR_WS_URL,
    PING_INTERVAL,
    RECONNECT_DELAY_INITIAL,
    RECONNECT_DELAY_MAX,
    RECONNECT_JITTER_MAX,
    WORKER_NAME,
    WORKER_WORKDIR,
    WORKER_USE_WORKTREES,
    WORKER_WORKTREE_ROOT,
    SUPERGOD_AUTH_TOKEN,
)
from supergod.shared.protocol import (
    PongMsg,
    WorkerOutputMsg,
    WorkerReadyMsg,
    WorkerTaskCompleteMsg,
    WorkerTaskErrorMsg,
    deserialize,
    serialize,
)
from supergod.worker.codex_runner import CodexError, run_codex
from supergod.worker import git_ops

log = logging.getLogger(__name__)


class WorkerDaemon:
    def __init__(
        self,
        name: str = WORKER_NAME,
        orchestrator_url: str = ORCHESTRATOR_WS_URL,
        workdir: str = WORKER_WORKDIR,
    ):
        self.name = name
        self.orchestrator_url = _with_auth_token(f"{orchestrator_url}/ws/worker")
        self.workdir = workdir
        self._ws = None
        self._current_task: asyncio.Task | None = None
        self._shutdown = asyncio.Event()

    async def run(self) -> None:
        delay = RECONNECT_DELAY_INITIAL
        while not self._shutdown.is_set():
            try:
                log.info("Connecting to %s", self.orchestrator_url)
                async with websockets.connect(
                    self.orchestrator_url,
                    ping_interval=PING_INTERVAL,
                    ping_timeout=PING_INTERVAL,
                ) as ws:
                    self._ws = ws
                    delay = RECONNECT_DELAY_INITIAL  # reset on success

                    # Always send ready after (re)connect
                    await ws.send(serialize(WorkerReadyMsg(name=self.name)))
                    log.info("Registered as %s", self.name)
                    await self._cleanup_stale_worktrees()

                    await self._message_loop(ws)

            except (ConnectionClosed, OSError) as e:
                self._ws = None
                jitter = random.uniform(0, RECONNECT_JITTER_MAX)
                wait = delay + jitter
                log.warning(
                    "Connection lost: %s. Reconnecting in %.1fs (delay=%ds + jitter=%.1fs)...",
                    e, wait, delay, jitter,
                )
                # Wait with shutdown check so we can exit promptly
                try:
                    await asyncio.wait_for(
                        self._shutdown.wait(), timeout=wait
                    )
                    # If we get here, shutdown was set
                    break
                except asyncio.TimeoutError:
                    pass
                delay = min(delay * 2, RECONNECT_DELAY_MAX)

    async def _cleanup_stale_worktrees(self) -> None:
        if not WORKER_USE_WORKTREES:
            return
        root = os.path.join(self.workdir, WORKER_WORKTREE_ROOT)
        if not os.path.isdir(root):
            return
        for name in os.listdir(root):
            path = os.path.join(root, name)
            if not os.path.isdir(path):
                continue
            try:
                await git_ops.remove_worktree(self.workdir, path)
            except Exception:
                # Best-effort cleanup. Worker can still proceed if one path fails.
                pass

    async def _message_loop(self, ws) -> None:
        async for raw in ws:
            try:
                msg = deserialize(raw)
            except (ValueError, Exception) as e:
                log.warning("Bad message from orchestrator: %s -- raw: %s", e, str(raw)[:200])
                continue

            match msg.type:
                case "task":
                    if self._current_task and not self._current_task.done():
                        log.warning("Already running a task, rejecting")
                        await self._safe_send(
                            ws,
                            serialize(
                                WorkerTaskErrorMsg(
                                    task_id=msg.id, error="Worker busy"
                                )
                            ),
                        )
                        continue
                    self._current_task = asyncio.create_task(
                        self._execute_task(ws, msg)
                    )
                    self._current_task.subtask_id = msg.id  # type: ignore[attr-defined]

                case "cancel":
                    if (
                        self._current_task
                        and not self._current_task.done()
                        and getattr(self._current_task, "subtask_id", None) == msg.task_id
                    ):
                        self._current_task.cancel()
                        log.info("Cancelled task %s", msg.task_id)
                    else:
                        log.info("Ignoring cancel for unknown task %s", msg.task_id)

                case "ping":
                    await self._safe_send(ws, serialize(PongMsg()))

                case _:
                    log.warning("Unknown message type from orchestrator: %s", msg.type)

    async def _safe_send(self, ws, data: str) -> bool:
        """Send a message, returning True on success. Logs and returns False on failure."""
        try:
            await ws.send(data)
            return True
        except (ConnectionClosed, OSError) as e:
            log.warning("Failed to send message: %s", e)
            return False

    async def _execute_task(self, ws, msg) -> None:
        task_id = msg.id
        branch = msg.branch
        execution_token = msg.execution_token
        log.info("Starting task %s on branch %s", task_id, branch)
        task_workdir = self.workdir
        used_worktree = False
        switched_main_repo = False
        worktree_dir = ""

        try:
            # Prepare git (skip remote ops if no remote configured)
            _has_remote = await git_ops.has_remote(self.workdir)
            if _has_remote:
                await git_ops.fetch(self.workdir)
                if WORKER_USE_WORKTREES:
                    safe_id = "".join(
                        c if c.isalnum() or c in ("-", "_") else "-"
                        for c in task_id
                    )
                    root = os.path.join(self.workdir, WORKER_WORKTREE_ROOT)
                    os.makedirs(root, exist_ok=True)
                    worktree_dir = os.path.join(root, safe_id)
                    await git_ops.create_worktree(
                        self.workdir, worktree_dir, branch
                    )
                    task_workdir = worktree_dir
                    used_worktree = True
                else:
                    await git_ops.checkout_branch(self.workdir, branch)
                    switched_main_repo = True
            else:
                log.info("No git remote configured, skipping fetch/branch")

            # Run codex
            async for event in run_codex(
                prompt=msg.prompt,
                workdir=task_workdir,
            ):
                await self._safe_send(
                    ws,
                    serialize(
                        WorkerOutputMsg(
                            task_id=task_id,
                            execution_token=execution_token,
                            event=event,
                        )
                    ),
                )

            # Commit results (push only if remote exists)
            await git_ops.add_all(task_workdir)
            await git_ops.commit(task_workdir, f"supergod: {task_id}")
            if _has_remote:
                await git_ops.push(task_workdir, branch)
            sha = await git_ops.get_head_sha(task_workdir)

            await self._safe_send(
                ws,
                serialize(
                    WorkerTaskCompleteMsg(
                        task_id=task_id,
                        execution_token=execution_token,
                        commit=sha,
                    )
                ),
            )
            log.info("Task %s complete, commit %s", task_id, sha)

        except CodexError as e:
            log.error("Task %s codex error: %s", task_id, e)
            await self._safe_send(
                ws,
                serialize(
                    WorkerTaskErrorMsg(
                        task_id=task_id,
                        execution_token=execution_token,
                        error=str(e),
                    )
                ),
            )
        except asyncio.CancelledError:
            log.info("Task %s cancelled", task_id)
            await self._safe_send(
                ws,
                serialize(
                    WorkerTaskErrorMsg(
                        task_id=task_id,
                        execution_token=execution_token,
                        error="Cancelled",
                    )
                ),
            )
        except Exception as e:
            log.error("Task %s unexpected error: %s", task_id, e, exc_info=True)
            await self._safe_send(
                ws,
                serialize(
                    WorkerTaskErrorMsg(
                        task_id=task_id,
                        execution_token=execution_token,
                        error=str(e),
                    )
                ),
            )
        finally:
            try:
                if used_worktree and worktree_dir:
                    await git_ops.remove_worktree(self.workdir, worktree_dir)
                elif switched_main_repo:
                    # Return to main branch in non-worktree mode.
                    await git_ops.checkout_main(self.workdir)
            except Exception:
                pass


def _with_auth_token(url: str) -> str:
    if not SUPERGOD_AUTH_TOKEN:
        return url
    parsed = urlparse(url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params.setdefault("token", SUPERGOD_AUTH_TOKEN)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(params),
            parsed.fragment,
        )
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Supergod worker daemon")
    parser.add_argument("--name", default=WORKER_NAME, help="Worker name")
    parser.add_argument(
        "--orchestrator",
        default=ORCHESTRATOR_WS_URL,
        help="Orchestrator WebSocket URL",
    )
    parser.add_argument("--workdir", default=WORKER_WORKDIR, help="Git repo path")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    daemon = WorkerDaemon(
        name=args.name,
        orchestrator_url=args.orchestrator,
        workdir=args.workdir,
    )

    loop = asyncio.new_event_loop()

    # Signal handlers only work on Unix
    if sys.platform != "win32":
        def shutdown_handler():
            daemon._shutdown.set()

        loop.add_signal_handler(signal.SIGTERM, shutdown_handler)
        loop.add_signal_handler(signal.SIGINT, shutdown_handler)

    try:
        loop.run_until_complete(daemon.run())
    except KeyboardInterrupt:
        daemon._shutdown.set()
    finally:
        loop.close()


if __name__ == "__main__":
    main()
