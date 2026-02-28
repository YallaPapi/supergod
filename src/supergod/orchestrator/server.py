"""Orchestrator server — FastAPI with WebSocket endpoints for workers and clients."""

from __future__ import annotations

import asyncio
import logging

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from supergod.shared.config import (
    DB_PATH,
    ORCHESTRATOR_HOST,
    ORCHESTRATOR_PORT,
    WORKER_WORKDIR,
)
from supergod.shared.protocol import (
    ClientCancelMsg,
    ClientStatusMsg,
    ClientTaskMsg,
    PingMsg,
    ProgressMsg,
    StatusResponseMsg,
    TaskAcceptedMsg,
    TaskInfo,
    TaskCompleteMsg,
    TaskFailedMsg,
    TaskStatus,
    WorkerInfo,
    WorkerListMsg,
    WorkerStatus,
    deserialize,
    serialize,
)
from supergod.orchestrator.brain import decompose_task, evaluate_results
from supergod.orchestrator.git_manager import merge_all_branches, run_tests
from supergod.orchestrator.scheduler import Scheduler
from supergod.orchestrator.state import StateDB

log = logging.getLogger(__name__)

app = FastAPI(title="Supergod Orchestrator")

# Global state — initialized in lifespan
db: StateDB | None = None
scheduler: Scheduler | None = None
client_connections: list[WebSocket] = []
workdir: str = WORKER_WORKDIR


@app.on_event("startup")
async def startup():
    global db, scheduler
    db = StateDB(DB_PATH)
    await db.init()
    scheduler = Scheduler(db)
    log.info("Orchestrator started")


@app.on_event("shutdown")
async def shutdown():
    if db:
        await db.close()


# --- Client WebSocket ---


@app.websocket("/ws/client")
async def client_ws(ws: WebSocket):
    await ws.accept()
    client_connections.append(ws)
    log.info("Client connected (%d total)", len(client_connections))

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = deserialize(raw)
            except ValueError as e:
                log.warning("Bad client message: %s", e)
                continue

            match msg.type:
                case "task":
                    await _handle_client_task(ws, msg)
                case "status":
                    await _handle_client_status(ws)
                case "cancel":
                    await _handle_client_cancel(ws, msg)

    except WebSocketDisconnect:
        client_connections.remove(ws)
        log.info("Client disconnected")


async def _handle_client_task(ws: WebSocket, msg: ClientTaskMsg):
    task_id = msg.task_id
    await db.create_task(task_id, msg.prompt)
    await ws.send_text(serialize(TaskAcceptedMsg(task_id=task_id)))

    # Decompose in background so we don't block the websocket
    asyncio.create_task(_process_task(task_id, msg.prompt))


async def _handle_client_status(ws: WebSocket):
    tasks = await db.get_all_tasks()
    workers = await db.get_all_workers()

    task_infos = []
    for t in tasks:
        subtasks = await db.get_subtasks_for_task(t["task_id"])
        completed = sum(1 for s in subtasks if s["status"] == TaskStatus.COMPLETED)
        task_infos.append(
            TaskInfo(
                task_id=t["task_id"],
                status=t["status"],
                prompt=t["prompt"],
                subtasks=len(subtasks),
                completed_subtasks=completed,
            )
        )

    worker_infos = [
        WorkerInfo(name=w["name"], status=w["status"])
        for w in workers
    ]

    await ws.send_text(
        serialize(StatusResponseMsg(tasks=task_infos, workers=worker_infos))
    )


async def _handle_client_cancel(ws: WebSocket, msg: ClientCancelMsg):
    await db.update_task_status(msg.task_id, TaskStatus.CANCELLED)
    # TODO: cancel in-progress subtasks on workers


# --- Worker WebSocket ---


@app.websocket("/ws/worker")
async def worker_ws(ws: WebSocket):
    await ws.accept()
    worker_name = None

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = deserialize(raw)
            except ValueError as e:
                log.warning("Bad worker message: %s", e)
                continue

            match msg.type:
                case "ready":
                    worker_name = msg.name
                    await scheduler.register_worker(worker_name, ws)

                case "output":
                    # Forward progress to all clients
                    progress = ProgressMsg(
                        task_id=msg.task_id,
                        subtask_id=msg.task_id,
                        worker=worker_name,
                        output=_extract_text(msg.event),
                    )
                    await _broadcast_to_clients(serialize(progress))

                case "task_complete":
                    await scheduler.handle_task_complete(
                        worker_name, msg.task_id, msg.commit
                    )
                    # Check if parent task has more work
                    await _check_task_progress(msg.task_id)

                case "task_error":
                    await scheduler.handle_task_error(
                        worker_name, msg.task_id, msg.error
                    )
                    await _check_task_progress(msg.task_id)

                case "pong":
                    pass

    except WebSocketDisconnect:
        if worker_name:
            await scheduler.unregister_worker(worker_name)
            log.info("Worker %s disconnected", worker_name)


# --- Task Processing Pipeline ---


async def _process_task(task_id: str, prompt: str):
    """Full task lifecycle: decompose → assign → merge → test."""
    try:
        await db.update_task_status(task_id, TaskStatus.DECOMPOSING)
        await _broadcast_to_clients(
            serialize(ProgressMsg(task_id=task_id, output="Decomposing task..."))
        )

        # Decompose using orchestrator's own Codex
        subtasks = await decompose_task(prompt, workdir)

        # Create subtasks in DB
        for st in subtasks:
            subtask_id = f"{task_id}-{st.id}"
            branch = f"task/{subtask_id}"
            await db.create_subtask(
                subtask_id=subtask_id,
                task_id=task_id,
                prompt=st.description,
                branch=branch,
                depends_on=[f"{task_id}-{d}" for d in st.depends_on],
            )

        await db.update_task_status(task_id, TaskStatus.ASSIGNED)

        # Start assigning ready subtasks
        assigned = await scheduler.try_assign_ready_subtasks(task_id)
        await _broadcast_to_clients(
            serialize(
                ProgressMsg(
                    task_id=task_id,
                    output=f"Decomposed into {len(subtasks)} subtasks, {assigned} assigned",
                )
            )
        )

    except Exception as e:
        log.error("Task processing failed: %s", e, exc_info=True)
        await db.update_task_status(task_id, TaskStatus.FAILED)
        await _broadcast_to_clients(
            serialize(TaskFailedMsg(task_id=task_id, error=str(e)))
        )


async def _check_task_progress(subtask_id: str):
    """Called when a subtask completes/fails. Check if we can assign more or finish."""
    # Find parent task
    subtasks_rows = None
    task_id = None

    # Get the subtask to find its parent
    all_tasks = await db.get_all_tasks()
    for t in all_tasks:
        subs = await db.get_subtasks_for_task(t["task_id"])
        for s in subs:
            if s["subtask_id"] == subtask_id:
                task_id = t["task_id"]
                subtasks_rows = subs
                break
        if task_id:
            break

    if not task_id:
        log.warning("Orphan subtask completion: %s", subtask_id)
        return

    # Try to assign more ready subtasks
    await scheduler.try_assign_ready_subtasks(task_id)

    # Check if all done
    if not await scheduler.all_subtasks_done(task_id):
        return

    # All subtasks finished — merge and test
    if await scheduler.any_subtask_failed(task_id):
        await db.update_task_status(task_id, TaskStatus.FAILED)
        await _broadcast_to_clients(
            serialize(
                TaskFailedMsg(
                    task_id=task_id, error="One or more subtasks failed"
                )
            )
        )
        return

    await _broadcast_to_clients(
        serialize(ProgressMsg(task_id=task_id, output="All subtasks done. Merging..."))
    )

    # Merge all branches
    branches = [s["branch"] for s in subtasks_rows if s["status"] == TaskStatus.COMPLETED]
    merge_ok, errors = await merge_all_branches(workdir, branches)

    if not merge_ok:
        await db.update_task_status(task_id, TaskStatus.FAILED)
        await _broadcast_to_clients(
            serialize(
                TaskFailedMsg(task_id=task_id, error=f"Merge failed: {errors}")
            )
        )
        return

    # Run tests
    await _broadcast_to_clients(
        serialize(ProgressMsg(task_id=task_id, output="Running tests..."))
    )
    test_ok, test_output = await run_tests(workdir)

    if test_ok:
        await db.update_task_status(
            task_id, TaskStatus.COMPLETED, summary="All tests passed"
        )
        await _broadcast_to_clients(
            serialize(TaskCompleteMsg(task_id=task_id, summary="All tests passed"))
        )
    else:
        # Use Codex to evaluate failures
        evaluation = await evaluate_results(
            original_prompt=(await db.get_task(task_id))["prompt"],
            test_output=test_output,
            workdir=workdir,
        )
        await db.update_task_status(
            task_id, TaskStatus.FAILED, summary=evaluation.get("summary", "")
        )
        await _broadcast_to_clients(
            serialize(
                TaskFailedMsg(
                    task_id=task_id,
                    error=f"Tests failed: {evaluation.get('summary', test_output[:500])}",
                )
            )
        )


# --- Helpers ---


def _extract_text(event: dict) -> str:
    """Extract readable text from a Codex JSONL event."""
    event_type = event.get("type", "")
    if event_type == "item.completed":
        item = event.get("item", {})
        content = item.get("content", [])
        for part in content:
            if part.get("type") == "text":
                return part.get("text", "")
    elif event_type == "turn.completed":
        return "[turn completed]"
    elif event_type == "error":
        return f"[error] {event.get('message', '')}"
    return ""


async def _broadcast_to_clients(msg: str):
    """Send a message to all connected clients."""
    disconnected = []
    for ws in client_connections:
        try:
            await ws.send_text(msg)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        client_connections.remove(ws)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Supergod orchestrator")
    parser.add_argument("--host", default=ORCHESTRATOR_HOST)
    parser.add_argument("--port", type=int, default=ORCHESTRATOR_PORT)
    parser.add_argument("--workdir", default=WORKER_WORKDIR, help="Orchestrator's repo clone")
    parser.add_argument("--db", default=DB_PATH, help="SQLite database path")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    global workdir, DB_PATH
    workdir = args.workdir

    import supergod.shared.config as cfg
    cfg.DB_PATH = args.db

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
