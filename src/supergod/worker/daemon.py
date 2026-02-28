"""Worker daemon — connects to orchestrator, executes tasks via Codex CLI."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

import websockets
from websockets.exceptions import ConnectionClosed

from supergod.shared.config import (
    ORCHESTRATOR_WS_URL,
    PING_INTERVAL,
    RECONNECT_DELAY_INITIAL,
    RECONNECT_DELAY_MAX,
    WORKER_NAME,
    WORKER_WORKDIR,
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
        self.orchestrator_url = f"{orchestrator_url}/ws/worker"
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

                    # Register
                    await ws.send(serialize(WorkerReadyMsg(name=self.name)))
                    log.info("Registered as %s", self.name)

                    await self._message_loop(ws)

            except (ConnectionClosed, OSError) as e:
                log.warning("Connection lost: %s. Reconnecting in %ds...", e, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_DELAY_MAX)

    async def _message_loop(self, ws) -> None:
        async for raw in ws:
            try:
                msg = deserialize(raw)
            except ValueError as e:
                log.warning("Bad message: %s", e)
                continue

            match msg.type:
                case "task":
                    if self._current_task and not self._current_task.done():
                        log.warning("Already running a task, rejecting")
                        await ws.send(
                            serialize(
                                WorkerTaskErrorMsg(
                                    task_id=msg.id, error="Worker busy"
                                )
                            )
                        )
                        continue
                    self._current_task = asyncio.create_task(
                        self._execute_task(ws, msg)
                    )

                case "cancel":
                    if self._current_task and not self._current_task.done():
                        self._current_task.cancel()
                        log.info("Cancelled task %s", msg.task_id)

                case "ping":
                    await ws.send(serialize(PongMsg()))

    async def _execute_task(self, ws, msg) -> None:
        task_id = msg.id
        branch = msg.branch
        log.info("Starting task %s on branch %s", task_id, branch)

        try:
            # Prepare git
            await git_ops.fetch(self.workdir)
            await git_ops.checkout_branch(self.workdir, branch)

            # Run codex
            async for event in run_codex(
                prompt=msg.prompt,
                workdir=self.workdir,
            ):
                await ws.send(
                    serialize(
                        WorkerOutputMsg(task_id=task_id, event=event)
                    )
                )

            # Commit and push results
            await git_ops.add_all(self.workdir)
            await git_ops.commit(self.workdir, f"supergod: {task_id}")
            await git_ops.push(self.workdir, branch)
            sha = await git_ops.get_head_sha(self.workdir)

            await ws.send(
                serialize(WorkerTaskCompleteMsg(task_id=task_id, commit=sha))
            )
            log.info("Task %s complete, commit %s", task_id, sha)

        except CodexError as e:
            log.error("Task %s codex error: %s", task_id, e)
            await ws.send(
                serialize(WorkerTaskErrorMsg(task_id=task_id, error=str(e)))
            )
        except asyncio.CancelledError:
            log.info("Task %s cancelled", task_id)
            await ws.send(
                serialize(WorkerTaskErrorMsg(task_id=task_id, error="Cancelled"))
            )
        except Exception as e:
            log.error("Task %s unexpected error: %s", task_id, e, exc_info=True)
            await ws.send(
                serialize(WorkerTaskErrorMsg(task_id=task_id, error=str(e)))
            )
        finally:
            # Return to main branch
            try:
                await git_ops.checkout_main(self.workdir)
            except Exception:
                pass


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

    def shutdown_handler():
        daemon._shutdown.set()

    loop.add_signal_handler(signal.SIGTERM, shutdown_handler)
    loop.add_signal_handler(signal.SIGINT, shutdown_handler)

    try:
        loop.run_until_complete(daemon.run())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
