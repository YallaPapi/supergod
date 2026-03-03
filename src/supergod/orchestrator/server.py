"""Orchestrator server -- FastAPI with WebSocket endpoints for workers and clients."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.responses import PlainTextResponse

from supergod.shared.config import (
    DB_PATH,
    DISPATCH_INTERVAL,
    ENABLE_STUCK_DETECTION,
    LEASE_SWEEP_INTERVAL,
    MAX_WORKERS_PER_TASK,
    PLANNING_INTERVAL,
    ORCHESTRATOR_HOST,
    ORCHESTRATOR_PORT,
    SUBTASK_LEASE_TIMEOUT,
    SUPERGOD_AUTH_TOKEN,
    WORKER_WORKDIR,
)
from supergod.shared.protocol import (
    ChatResponseMsg,
    ClientCancelMsg,
    ClientChatMsg,
    ClientPauseMsg,
    ClientResumeMsg,
    ClientStartFromBriefMsg,
    ClientStatusMsg,
    ClientTaskMsg,
    ProgressMsg,
    StatusResponseMsg,
    TaskAcceptedMsg,
    TaskInfo,
    TaskCompleteMsg,
    TaskFailedMsg,
    TaskReviewMsg,
    TaskStatus,
    WorkerInfo,
    WorkerListMsg,
    WorkerCancelMsg,
    WorkerStatus,
    deserialize,
    new_id,
    serialize,
)
from supergod.orchestrator.brain import decompose_task, replan_check
from supergod.orchestrator.git_manager import (
    merge_all_branches_with_report,
    run_tests,
)
from supergod.orchestrator.scheduler import Scheduler
from supergod.orchestrator.stuck_detector import StuckDetector
from supergod.orchestrator.state import StateDB
from supergod.orchestrator.validation import validate_completed_subtask
from supergod.skills.runtime import build_worker_subtask_prompt

log = logging.getLogger(__name__)

# Global state -- initialized in lifespan
db: StateDB | None = None
scheduler: Scheduler | None = None
client_connections: list[WebSocket] = []
workdir: str = WORKER_WORKDIR
db_path: str = DB_PATH
chat_sessions: dict[str, dict] = {}
_finalizing_tasks: set[str] = set()
_finalize_lock = asyncio.Lock()
_task_completion_counts: dict[str, int] = {}
stuck_detector = StuckDetector()
_lease_watchdog_task: asyncio.Task | None = None
_dispatch_task: asyncio.Task | None = None
_metrics: dict[str, float] = {
    "tasks_submitted_total": 0.0,
    "tasks_completed_total": 0.0,
    "tasks_failed_total": 0.0,
    "subtasks_assigned_total": 0.0,
    "subtasks_completed_total": 0.0,
    "subtasks_failed_total": 0.0,
    "retries_total": 0.0,
    "stuck_kills_total": 0.0,
    "lease_timeouts_total": 0.0,
    "validation_failures_total": 0.0,
    "merge_conflicts_total": 0.0,
}
STATIC_DIR = Path(__file__).resolve().parent / "static"
MISSION_HTML = STATIC_DIR / "mission_control.html"


@asynccontextmanager
async def lifespan(_: FastAPI):
    global db, scheduler, _lease_watchdog_task, _dispatch_task
    db = StateDB(db_path)
    await db.init()
    await db.reset_worker_leases()
    await _consistency_sweep()
    scheduler = Scheduler(db)
    await scheduler.start_ping_loop()
    _lease_watchdog_task = asyncio.create_task(_lease_watchdog_loop())
    _dispatch_task = asyncio.create_task(_dispatch_loop())
    await _resume_in_progress_tasks()
    log.info("Orchestrator started")
    try:
        yield
    finally:
        if _lease_watchdog_task:
            _lease_watchdog_task.cancel()
            try:
                await _lease_watchdog_task
            except asyncio.CancelledError:
                pass
            _lease_watchdog_task = None
        if _dispatch_task:
            _dispatch_task.cancel()
            try:
                await _dispatch_task
            except asyncio.CancelledError:
                pass
            _dispatch_task = None
        if scheduler:
            await scheduler.stop_ping_loop()
        if db:
            await db.close()


app = FastAPI(title="Supergod Orchestrator", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    tasks = await db.get_all_tasks()
    workers = await db.get_all_workers()
    return {
        "status": "ok",
        "tasks": len(tasks),
        "workers": len(workers),
        "idle_workers": sum(1 for w in workers if w["status"] == WorkerStatus.IDLE),
        "busy_workers": sum(1 for w in workers if w["status"] == WorkerStatus.BUSY),
        "max_workers_per_task": MAX_WORKERS_PER_TASK,
    }


@app.get("/snapshot")
async def snapshot(request: Request):
    if not _is_http_authorized(request):
        return {"error": "unauthorized", "tasks": [], "workers": []}
    tasks = await db.get_all_tasks()
    workers = await db.get_all_workers()

    task_infos = []
    for t in tasks:
        subtasks = await db.get_subtasks_for_task(t["task_id"])
        completed = sum(1 for s in subtasks if s["status"] == TaskStatus.COMPLETED)
        task_infos.append(
            {
                "task_id": t["task_id"],
                "status": t["status"],
                "prompt": t["prompt"],
                "priority": t.get("priority", 100),
                "subtasks": len(subtasks),
                "completed_subtasks": completed,
                "updated_at": t.get("updated_at"),
            }
        )

    worker_infos = [
        {
            "name": w["name"],
            "status": w["status"],
            "current_subtask": w.get("current_subtask"),
            "last_seen": w.get("last_seen"),
        }
        for w in workers
    ]
    return {"tasks": task_infos, "workers": worker_infos}


@app.get("/mission", response_class=HTMLResponse)
async def mission_control(request: Request):
    if not _is_http_authorized(request):
        return HTMLResponse("Unauthorized", status_code=401)
    if not MISSION_HTML.exists():
        return HTMLResponse("Mission dashboard asset not found", status_code=500)
    return HTMLResponse(MISSION_HTML.read_text(encoding="utf-8"))


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    tasks = await db.get_all_tasks()
    workers = await db.get_all_workers()
    status_counts: dict[str, int] = {}
    for t in tasks:
        status_counts[t["status"]] = status_counts.get(t["status"], 0) + 1
    worker_counts: dict[str, int] = {}
    for w in workers:
        worker_counts[w["status"]] = worker_counts.get(w["status"], 0) + 1

    lines = []
    for key in sorted(_metrics):
        lines.append(f"supergod_{key} {_metrics[key]}")
    for status, count in sorted(status_counts.items()):
        lines.append(f'supergod_tasks_status{{status="{status}"}} {count}')
    for status, count in sorted(worker_counts.items()):
        lines.append(f'supergod_workers_status{{status="{status}"}} {count}')
    lines.append(f"supergod_dispatch_interval_seconds {DISPATCH_INTERVAL}")
    lines.append(f"supergod_subtask_lease_timeout_seconds {SUBTASK_LEASE_TIMEOUT}")
    lines.append(f"supergod_max_workers_per_task {MAX_WORKERS_PER_TASK}")
    return "\n".join(lines) + "\n"


@app.get("/task/{task_id}/events")
async def task_events(task_id: str, request: Request, limit: int = 200):
    if not _is_http_authorized(request):
        return {"task_id": task_id, "events": []}
    task = await db.get_task(task_id)
    if not task:
        return {"task_id": task_id, "events": []}
    events = await db.get_task_events(task_id, limit=limit)
    return {"task_id": task_id, "events": events}

# --- Client WebSocket ---


@app.websocket("/ws/client")
async def client_ws(ws: WebSocket):
    if not _is_authorized(ws):
        await ws.close(code=1008)
        return
    await ws.accept()
    client_connections.append(ws)
    log.info("Client connected (%d total)", len(client_connections))

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = deserialize(raw)
            except (ValueError, Exception) as e:
                log.warning("Bad client message: %s — raw: %s", e, raw[:200])
                continue

            match msg.type:
                case "task":
                    await _handle_client_task(ws, msg)
                case "status":
                    await _handle_client_status(ws)
                case "cancel":
                    await _handle_client_cancel(ws, msg)
                case "pause":
                    await _handle_client_pause(ws, msg)
                case "resume":
                    await _handle_client_resume(ws, msg)
                case "chat":
                    await _handle_client_chat(ws, msg)
                case "start_from_brief":
                    await _handle_client_start_from_brief(ws, msg)
                case _:
                    log.warning("Unknown client message type: %s", msg.type)

    except WebSocketDisconnect:
        log.info("Client disconnected")
    except Exception as e:
        log.error("Client WebSocket error: %s", e, exc_info=True)
    finally:
        if ws in client_connections:
            client_connections.remove(ws)


async def _handle_client_task(ws: WebSocket, msg: ClientTaskMsg):
    priority = max(0, min(1000, int(msg.priority)))
    await _accept_and_process_task(ws, msg.task_id, msg.prompt, priority)


async def _accept_and_process_task(
    ws: WebSocket, task_id: str, prompt: str, priority: int = 100
) -> None:
    await db.create_task(task_id, prompt, priority=priority)
    _metric_inc("tasks_submitted_total")
    await _save_checkpoint(task_id, "task_accepted", {"prompt": prompt[:500]})
    await ws.send_text(serialize(TaskAcceptedMsg(task_id=task_id)))
    # Decompose in background so we don't block the websocket.
    asyncio.create_task(_process_task(task_id, prompt))


async def _resume_in_progress_tasks() -> None:
    resumable = await db.get_resumable_tasks()
    if not resumable:
        return
    log.info("Resuming %d in-progress task(s)", len(resumable))

    for task in resumable:
        task_id = task["task_id"]
        checkpoint = await db.get_latest_checkpoint(task_id)
        step = checkpoint["step"] if checkpoint else "unknown"
        log.info("Resuming task %s from checkpoint step: %s", task_id, step)

        if step in {"task_accepted", "decomposing"}:
            subtasks = await db.get_subtasks_for_task(task_id)
            if not subtasks:
                asyncio.create_task(_process_task(task_id, task["prompt"]))
            else:
                await _resume_assignment_from_checkpoint(
                    task_id, f"checkpoint:{step}"
                )
            continue

        if step in {
            "decomposed",
            "subtask_assigned",
            "subtask_completed",
            "subtask_failed",
            "resumed",
        }:
            await _resume_assignment_from_checkpoint(
                task_id, f"checkpoint:{step}"
            )
            continue

        if step in {"merging", "testing", "task_failed"}:
            await _requeue_running_subtasks(
                task_id, "Requeued after orchestrator restart"
            )
            if await db.all_terminal(task_id):
                asyncio.create_task(_finalize_task(task_id))
            else:
                await _resume_assignment_from_checkpoint(
                    task_id, f"checkpoint:{step}"
                )
            continue

        if step == "task_cancelled":
            await db.update_task_status(task_id, TaskStatus.CANCELLED)
            continue

        # Fallback for unknown/no checkpoint
        subtasks = await db.get_subtasks_for_task(task_id)
        if not subtasks:
            asyncio.create_task(_process_task(task_id, task["prompt"]))
        else:
            await _resume_assignment_from_checkpoint(
                task_id, "fallback:unknown_checkpoint"
            )


async def _consistency_sweep() -> None:
    """Repair obvious task/subtask inconsistencies on startup."""
    repaired = 0

    orphans = await db.get_orphan_subtasks()
    for subtask in orphans:
        await db.delete_subtask(subtask["subtask_id"])
        repaired += 1

    tasks = await db.get_all_tasks()
    for task in tasks:
        task_id = task["task_id"]
        subtasks = await db.get_subtasks_for_task(task_id)

        for subtask in subtasks:
            if (
                subtask["status"] == TaskStatus.RUNNING
                and not subtask.get("worker_name")
            ):
                attempts = int(subtask.get("attempt_count") or 0)
                await db.update_subtask(
                    subtask["subtask_id"],
                    status=TaskStatus.PENDING,
                    execution_token="",
                    attempt_count=attempts + 1,
                    failure_category="consistency_repair",
                    error_message=(
                        "Startup consistency sweep requeued running subtask without worker lease"
                    ),
                )
                repaired += 1

        task_status = task["status"]
        nonterminal = [
            s
            for s in subtasks
            if s["status"]
            not in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
                TaskStatus.BLOCKED,
            )
        ]
        if task_status == TaskStatus.CANCELLED and nonterminal:
            for s in nonterminal:
                await db.update_subtask(
                    s["subtask_id"],
                    status=TaskStatus.CANCELLED,
                    execution_token="",
                    failure_category="cancelled",
                    error_message="Cancelled during startup consistency sweep",
                )
                repaired += 1
        elif task_status in (TaskStatus.COMPLETED, TaskStatus.FAILED) and nonterminal:
            for s in nonterminal:
                await db.update_subtask(
                    s["subtask_id"],
                    status=TaskStatus.CANCELLED,
                    execution_token="",
                    failure_category="consistency_repair",
                    error_message="Task already terminal during startup sweep",
                )
                repaired += 1
        elif task_status == TaskStatus.PENDING and subtasks:
            await db.update_task_status(task_id, TaskStatus.ASSIGNED)
            repaired += 1

    if repaired:
        log.info("Startup consistency sweep repaired %d record(s)", repaired)


async def _resume_assignment_from_checkpoint(
    task_id: str, reason: str
) -> None:
    await _requeue_running_subtasks(task_id, "Requeued after orchestrator restart")
    if await db.all_terminal(task_id):
        asyncio.create_task(_finalize_task(task_id))
        return
    await db.update_task_status(task_id, TaskStatus.ASSIGNED)
    async def _assign_once():
        assigned = await scheduler.try_assign_ready_subtasks(task_id)
        if assigned:
            _metric_inc("subtasks_assigned_total", assigned)
    asyncio.create_task(_assign_once())
    await _save_checkpoint(task_id, "resumed", {"reason": reason})


async def _requeue_running_subtasks(task_id: str, message: str) -> None:
    subtasks = await db.get_subtasks_for_task(task_id)
    for subtask in subtasks:
        if subtask["status"] != TaskStatus.RUNNING:
            continue
        attempts = int(subtask.get("attempt_count") or 0)
        await db.update_subtask(
            subtask["subtask_id"],
            status=TaskStatus.PENDING,
            worker_name=None,
            execution_token="",
            attempt_count=attempts + 1,
            failure_category="transient_infra",
            error_message=message,
        )


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
                priority=t.get("priority", 100),
                subtasks=len(subtasks),
                completed_subtasks=completed,
            )
        )

    worker_infos = [
        WorkerInfo(
            name=w["name"],
            status=w["status"],
            current_subtask=w.get("current_subtask"),
            last_seen=w.get("last_seen"),
        )
        for w in workers
    ]

    await ws.send_text(
        serialize(StatusResponseMsg(tasks=task_infos, workers=worker_infos))
    )


async def _handle_client_cancel(ws: WebSocket, msg: ClientCancelMsg):
    _task_completion_counts.pop(msg.task_id, None)
    await db.update_task_status(msg.task_id, TaskStatus.CANCELLED)
    subtasks = await db.get_subtasks_for_task(msg.task_id)
    cancelled_running = 0
    for subtask in subtasks:
        status = subtask["status"]
        if status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.BLOCKED,
        ):
            continue

        await db.update_subtask(
            subtask["subtask_id"],
            status=TaskStatus.CANCELLED,
            failure_category="cancelled",
            error_message="Cancelled by user",
        )
        if status == TaskStatus.RUNNING and subtask.get("worker_name"):
            cancelled_running += 1
            wc = scheduler.workers.get(subtask["worker_name"])
            if wc:
                try:
                    await wc.ws.send_text(
                        serialize(
                            WorkerCancelMsg(task_id=subtask["subtask_id"])
                        )
                    )
                except Exception:
                    pass
    await _broadcast_to_clients(
        serialize(
            ProgressMsg(
                task_id=msg.task_id,
                output=(
                    "Cancellation requested. "
                    f"Sent stop to {cancelled_running} running subtasks."
                ),
            )
        )
    )
    await _broadcast_to_clients(
        serialize(
            TaskFailedMsg(
                task_id=msg.task_id,
                error="Task cancelled by user",
            )
        )
    )
    await _save_checkpoint(
        msg.task_id,
        "task_cancelled",
        {"cancelled_running": cancelled_running},
    )


async def _handle_client_pause(ws: WebSocket, msg: ClientPauseMsg):
    task = await db.get_task(msg.task_id)
    if not task:
        return
    if task["status"] in (
        TaskStatus.CANCELLED,
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
    ):
        return
    await db.update_task_status(msg.task_id, TaskStatus.PAUSED)
    await _save_checkpoint(msg.task_id, "task_paused", {})
    await _broadcast_to_clients(
        serialize(
            ProgressMsg(
                task_id=msg.task_id,
                output="Task paused. Running subtasks may finish; new assignments are paused.",
            )
        )
    )


async def _handle_client_resume(ws: WebSocket, msg: ClientResumeMsg):
    task = await db.get_task(msg.task_id)
    if not task:
        return
    if task["status"] != TaskStatus.PAUSED:
        return
    if await db.all_terminal(msg.task_id):
        asyncio.create_task(_finalize_task(msg.task_id))
        return
    await db.update_task_status(msg.task_id, TaskStatus.ASSIGNED)
    await _save_checkpoint(msg.task_id, "task_resumed", {})
    assigned = await scheduler.try_assign_ready_subtasks_with_limit(
        msg.task_id, max_assign=1
    )
    if assigned:
        _metric_inc("subtasks_assigned_total", assigned)
    await _broadcast_to_clients(
        serialize(
            ProgressMsg(
                task_id=msg.task_id,
                output="Task resumed.",
            )
        )
    )


async def _handle_client_chat(ws: WebSocket, msg: ClientChatMsg):
    session = chat_sessions.setdefault(
        msg.session_id,
        {
            "goal": "",
            "constraints": "",
            "acceptance": "",
            "draft_prompt": "",
        },
    )
    text = msg.message.strip()
    if not text:
        await ws.send_text(
            serialize(
                ChatResponseMsg(
                    session_id=msg.session_id,
                    reply="Share the goal first, then constraints and acceptance criteria.",
                )
            )
        )
        return

    # Treat "start" as an in-chat start shortcut.
    if text.lower() in {"start", "/start"}:
        if not session.get("draft_prompt"):
            await ws.send_text(
                serialize(
                    ChatResponseMsg(
                        session_id=msg.session_id,
                        reply=(
                            "No draft brief is ready yet. "
                            "Tell me the goal first."
                        ),
                    )
                )
            )
            return
        await _start_brief_task(ws, msg.session_id)
        return

    reply, ready = _update_chat_session(session, text)
    await ws.send_text(
        serialize(
            ChatResponseMsg(
                session_id=msg.session_id,
                reply=reply,
                ready_to_start=ready,
                draft_prompt=session.get("draft_prompt", ""),
            )
        )
    )


async def _handle_client_start_from_brief(
    ws: WebSocket, msg: ClientStartFromBriefMsg
):
    await _start_brief_task(ws, msg.session_id)


async def _start_brief_task(ws: WebSocket, session_id: str):
    session = chat_sessions.get(session_id)
    if not session or not session.get("draft_prompt"):
        await ws.send_text(
            serialize(
                ChatResponseMsg(
                    session_id=session_id,
                    reply=(
                        "This session has no ready brief. Keep chatting first."
                    ),
                )
            )
        )
        return
    task_id = new_id()
    await _accept_and_process_task(ws, task_id, session["draft_prompt"])


def _update_chat_session(session: dict, message: str) -> tuple[str, bool]:
    if not session.get("goal"):
        session["goal"] = message
        return (
            "Captured goal. Next, list constraints and context "
            "(tech stack, budget, deadlines, hard requirements).",
            False,
        )
    if not session.get("constraints"):
        session["constraints"] = message
        return (
            "Captured constraints. Now define acceptance criteria "
            "(how we know this is done).",
            False,
        )
    if not session.get("acceptance"):
        session["acceptance"] = message
        session["draft_prompt"] = _build_brief_prompt(session)
        return (
            "Brief is ready. Review draft below, then send `/start` "
            "or `start_from_brief`.\n\n"
            + session["draft_prompt"]
        ), True

    # After draft is ready, new messages are appended as extra constraints.
    session["constraints"] = (
        f"{session['constraints']}\nAdditional note: {message}"
    )
    session["draft_prompt"] = _build_brief_prompt(session)
    return (
        "Updated the brief with your new note. Send `/start` when ready.\n\n"
        + session["draft_prompt"]
    ), True


def _build_brief_prompt(session: dict) -> str:
    return (
        "Goal:\n"
        f"{session.get('goal', '').strip()}\n\n"
        "Constraints and context:\n"
        f"{session.get('constraints', '').strip()}\n\n"
        "Acceptance criteria:\n"
        f"{session.get('acceptance', '').strip()}\n\n"
        "Instructions:\n"
        "- Decompose into independent subtasks when possible.\n"
        "- Continue independent work if one subtask fails.\n"
        "- Provide clear failure reasons and required user actions."
    )


# --- Worker WebSocket ---


@app.websocket("/ws/worker")
async def worker_ws(ws: WebSocket):
    if not _is_authorized(ws):
        await ws.close(code=1008)
        return
    await ws.accept()
    worker_name = None

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = deserialize(raw)
            except (ValueError, Exception) as e:
                log.warning("Bad worker message: %s — raw: %s", e, raw[:200])
                continue

            match msg.type:
                case "ready":
                    worker_name = msg.name
                    await scheduler.register_worker(worker_name, ws)

                case "output":
                    await db.touch_subtask(msg.task_id)
                    # Forward progress to all clients
                    output_text = _extract_text(msg.event)
                    if (
                        ENABLE_STUCK_DETECTION
                        and worker_name
                        and stuck_detector.feed(msg.task_id, output_text)
                    ):
                        _metric_inc("stuck_kills_total")
                        log.warning(
                            "Stuck output detected from %s on %s, cancelling",
                            worker_name,
                            msg.task_id,
                        )
                        wc = scheduler.workers.get(worker_name)
                        if wc:
                            try:
                                await wc.ws.send_text(
                                    serialize(WorkerCancelMsg(task_id=msg.task_id))
                                )
                            except Exception:
                                pass
                        await scheduler.handle_task_error(
                            worker_name,
                            msg.task_id,
                            "Stuck: repetitive output detected",
                            msg.execution_token,
                        )
                        await _save_checkpoint_for_subtask(
                            msg.task_id,
                            "subtask_failed",
                            {"reason": "stuck_detection"},
                        )
                        await _check_task_progress(msg.task_id)
                        continue
                    progress = ProgressMsg(
                        task_id=msg.task_id,
                        subtask_id=msg.task_id,
                        worker=worker_name,
                        output=output_text,
                    )
                    await _broadcast_to_clients(serialize(progress))

                case "worker_task_complete":
                    _metric_inc("subtasks_completed_total")
                    await scheduler.handle_task_complete(
                        worker_name,
                        msg.task_id,
                        msg.commit,
                        msg.execution_token,
                    )
                    stuck_detector.clear(msg.task_id)
                    await _save_checkpoint_for_subtask(
                        msg.task_id,
                        "subtask_completed",
                        {
                            "worker": worker_name,
                            "commit": msg.commit,
                            "execution_token": msg.execution_token,
                        },
                    )
                    # Check if parent task has more work
                    await _check_task_progress(msg.task_id)

                case "task_error":
                    _metric_inc("subtasks_failed_total")
                    await scheduler.handle_task_error(
                        worker_name,
                        msg.task_id,
                        msg.error,
                        msg.execution_token,
                    )
                    subtask_row = await db.get_subtask(msg.task_id)
                    if (
                        subtask_row
                        and subtask_row["status"] == TaskStatus.PENDING
                        and int(subtask_row.get("attempt_count") or 0) > 0
                    ):
                        _metric_inc("retries_total")
                    stuck_detector.clear(msg.task_id)
                    await _save_checkpoint_for_subtask(
                        msg.task_id,
                        "subtask_failed",
                        {
                            "worker": worker_name,
                            "error": msg.error[:500],
                            "execution_token": msg.execution_token,
                        },
                    )
                    await _check_task_progress(msg.task_id)

                case "pong":
                    if worker_name:
                        scheduler.record_pong(worker_name)

                case _:
                    log.warning("Unknown worker message type: %s", msg.type)

    except WebSocketDisconnect:
        log.info("Worker %s disconnected (WebSocketDisconnect)", worker_name or "unknown")
    except Exception as e:
        log.error("Worker %s WebSocket error: %s", worker_name or "unknown", e, exc_info=True)
    finally:
        if worker_name:
            await scheduler.unregister_worker(worker_name)


# --- Task Processing Pipeline ---


async def _process_task(task_id: str, prompt: str):
    """Full task lifecycle: decompose -> assign -> merge -> test."""
    try:
        _task_completion_counts[task_id] = 0
        await db.update_task_status(task_id, TaskStatus.DECOMPOSING)
        await _save_checkpoint(task_id, "decomposing", {})
        await _broadcast_to_clients(
            serialize(ProgressMsg(task_id=task_id, output="Decomposing task..."))
        )

        # Decompose using orchestrator's own Codex
        subtasks = await decompose_task(prompt, workdir)
        task = await db.get_task(task_id)
        if task and task["status"] == TaskStatus.CANCELLED:
            log.info("Task %s cancelled during decomposition", task_id)
            return

        # Create subtasks in DB
        for st in subtasks:
            subtask_id = f"{task_id}-{st.id}"
            branch = f"task/{subtask_id}"
            worker_prompt, skill_meta = build_worker_subtask_prompt(
                task_prompt=prompt,
                subtask_prompt=st.description,
                repo_root=workdir,
            )
            await db.create_subtask(
                subtask_id=subtask_id,
                task_id=task_id,
                prompt=worker_prompt,
                branch=branch,
                depends_on=[f"{task_id}-{d}" for d in st.depends_on],
            )
            await db.add_task_event(
                task_id,
                "skill_injection",
                {
                    "subtask_id": subtask_id,
                    "packs": skill_meta.get("selected_packs", []),
                    "skills": skill_meta.get("selected_skills", []),
                    "profile": skill_meta.get("profile", "default"),
                },
            )
        await _save_checkpoint(
            task_id,
            "decomposed",
            {"subtask_count": len(subtasks)},
        )

        await db.update_task_status(task_id, TaskStatus.ASSIGNED)

        # Start assigning ready subtasks
        assigned = await scheduler.try_assign_ready_subtasks_with_limit(
            task_id, max_assign=1
        )
        if assigned:
            _metric_inc("subtasks_assigned_total", assigned)
        if assigned:
            await _save_checkpoint(
                task_id,
                "subtask_assigned",
                {"assigned_count": assigned},
            )
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
        _metric_inc("tasks_failed_total")
        await _save_checkpoint(
            task_id,
            "task_failed",
            {"error": str(e)[:500]},
        )
        await _broadcast_to_clients(
            serialize(TaskFailedMsg(task_id=task_id, error=str(e)))
        )


async def _check_task_progress(subtask_id: str):
    """Called when a subtask completes/fails. Check if we can assign more or finish."""
    subtask = await db.get_subtask(subtask_id)
    if not subtask:
        log.warning("Orphan subtask completion: %s", subtask_id)
        return
    task_id = subtask["task_id"]

    task = await db.get_task(task_id)
    if not task:
        return
    if task["status"] in (TaskStatus.CANCELLED, TaskStatus.PAUSED):
        return

    # Try to assign more ready subtasks.
    newly_assigned = await scheduler.try_assign_ready_subtasks_with_limit(
        task_id, max_assign=1
    )
    if newly_assigned:
        _metric_inc("subtasks_assigned_total", newly_assigned)
    if not await scheduler.all_subtasks_done(task_id):
        await _maybe_replan_task(task_id)
        return

    async with _finalize_lock:
        if task_id in _finalizing_tasks:
            return
        _finalizing_tasks.add(task_id)
    try:
        await _finalize_task(task_id)
    finally:
        async with _finalize_lock:
            _finalizing_tasks.discard(task_id)


async def _finalize_task(task_id: str):
    task = await db.get_task(task_id)
    if not task:
        _task_completion_counts.pop(task_id, None)
        return
    if task["status"] in (TaskStatus.COMPLETED, TaskStatus.FAILED):
        _task_completion_counts.pop(task_id, None)
        return

    subtasks_rows = await db.get_subtasks_for_task(task_id)
    completed = [
        s for s in subtasks_rows if s["status"] == TaskStatus.COMPLETED
    ]
    failed = [s for s in subtasks_rows if s["status"] == TaskStatus.FAILED]
    blocked = [s for s in subtasks_rows if s["status"] == TaskStatus.BLOCKED]
    cancelled = [
        s for s in subtasks_rows if s["status"] == TaskStatus.CANCELLED
    ]

    if task["status"] == TaskStatus.CANCELLED:
        await _broadcast_to_clients(
            serialize(
                TaskFailedMsg(task_id=task_id, error="Task cancelled by user")
            )
        )
        return

    await _broadcast_to_clients(
        serialize(
            ProgressMsg(
                task_id=task_id,
                output="All subtasks terminal. Merging completed work...",
            )
        )
    )

    # Merge completed branches (partial results are still valuable).
    branches = [s["branch"] for s in completed]
    merge_ok = True
    merge_errors: list[str] = []
    test_ok = False
    test_output = "Tests were not run."

    from supergod.worker.git_ops import has_remote as _has_remote

    has_remote_configured = await _has_remote(workdir)

    invalid_outputs = await _apply_validation_gates(
        task_id, completed, has_remote_configured
    )
    if invalid_outputs:
        await _broadcast_to_clients(
            serialize(
                ProgressMsg(
                    task_id=task_id,
                    output=(
                        f"Validation gates failed for {len(invalid_outputs)} subtask(s); "
                        "continuing with remaining valid outputs."
                    ),
                )
            )
        )
        subtasks_rows = await db.get_subtasks_for_task(task_id)
        completed = [
            s for s in subtasks_rows if s["status"] == TaskStatus.COMPLETED
        ]
        failed = [s for s in subtasks_rows if s["status"] == TaskStatus.FAILED]
        blocked = [s for s in subtasks_rows if s["status"] == TaskStatus.BLOCKED]
        cancelled = [
            s for s in subtasks_rows if s["status"] == TaskStatus.CANCELLED
        ]
    branches = [s["branch"] for s in completed]

    if has_remote_configured and branches:
        await _save_checkpoint(task_id, "merging", {"branch_count": len(branches)})
        merge_report = await merge_all_branches_with_report(workdir, branches)
        merge_errors = merge_report["errors"]
        failed_merge_map = merge_report["failed"]
        merged_branches = merge_report["merged"]
        if failed_merge_map:
            log.error(
                "Task %s merge completed with conflicts on %d branch(es)",
                task_id,
                len(failed_merge_map),
            )
            isolated = await _isolate_failed_merge_branches(
                task_id, failed_merge_map
            )
            if isolated:
                await _broadcast_to_clients(
                    serialize(
                        ProgressMsg(
                            task_id=task_id,
                            output=(
                                f"Isolated {len(isolated)} conflicted branch(es); "
                                "continuing with remaining merged outputs."
                            ),
                        )
                    )
                )
            # Recompute task state after isolation.
            subtasks_rows = await db.get_subtasks_for_task(task_id)
            completed = [
                s for s in subtasks_rows if s["status"] == TaskStatus.COMPLETED
            ]
            failed = [s for s in subtasks_rows if s["status"] == TaskStatus.FAILED]
            blocked = [s for s in subtasks_rows if s["status"] == TaskStatus.BLOCKED]
            cancelled = [
                s for s in subtasks_rows if s["status"] == TaskStatus.CANCELLED
            ]
            branches = [s["branch"] for s in completed]
            merge_ok = bool(merged_branches)
        else:
            merge_ok = True
    else:
        log.info("Skipping merge (no remote or no completed branch)")

    if merge_ok:
        await _save_checkpoint(task_id, "testing", {})
        await _broadcast_to_clients(
            serialize(ProgressMsg(task_id=task_id, output="Running tests..."))
        )
        test_ok, test_output = await run_tests(workdir)

    if not failed and not blocked and not cancelled and merge_ok and test_ok:
        await db.update_task_status(
            task_id,
            TaskStatus.COMPLETED,
            summary="All subtasks completed and tests passed",
        )
        _metric_inc("tasks_completed_total")
        await _broadcast_to_clients(
            serialize(
                TaskCompleteMsg(
                    task_id=task_id,
                    summary="All subtasks completed and tests passed",
                )
            )
        )
        await _save_checkpoint(task_id, "task_completed", {})
        _task_completion_counts.pop(task_id, None)
        return

    review = _build_task_review(
        task_id=task_id,
        completed=completed,
        failed=failed,
        blocked=blocked,
        cancelled=cancelled,
        merge_errors=merge_errors,
        test_ok=test_ok,
        test_output=test_output,
    )
    await db.update_task_status(
        task_id,
        TaskStatus.FAILED,
        summary=review["summary"],
    )
    _metric_inc("tasks_failed_total")
    await _broadcast_to_clients(
        serialize(
            TaskReviewMsg(
                task_id=task_id,
                completed_count=review["completed_count"],
                failed_count=review["failed_count"],
                blocked_count=review["blocked_count"],
                failed_subtasks=review["failed_subtasks"],
                blocked_subtasks=review["blocked_subtasks"],
                test_summary=review["test_summary"],
            )
        )
    )
    await _broadcast_to_clients(
        serialize(TaskFailedMsg(task_id=task_id, error=review["summary"]))
    )
    await _save_checkpoint(
        task_id,
        "task_failed",
        {"summary": review["summary"][:500]},
    )
    _task_completion_counts.pop(task_id, None)


def _normalize_dep_id(task_id: str, dep_id: str) -> str:
    dep_id = dep_id.strip()
    if not dep_id:
        return ""
    if dep_id.startswith(f"{task_id}-"):
        return dep_id
    return f"{task_id}-{dep_id}"


def _has_dependency_cycle(dep_graph: dict[str, set[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def _visit(node: str) -> bool:
        if node in visited:
            return False
        if node in visiting:
            return True
        visiting.add(node)
        for dep in dep_graph.get(node, set()):
            if dep in dep_graph and _visit(dep):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(_visit(node) for node in dep_graph)


async def _cancel_remaining_subtasks(task_id: str, reason: str) -> int:
    rows = await db.get_subtasks_for_task(task_id)
    cancelled = 0
    for row in rows:
        if row["status"] not in (TaskStatus.PENDING, TaskStatus.RUNNING):
            continue
        worker_name = row.get("worker_name")
        if row["status"] == TaskStatus.RUNNING and worker_name:
            wc = scheduler.workers.get(worker_name)
            if wc:
                try:
                    await wc.ws.send_text(
                        serialize(WorkerCancelMsg(task_id=row["subtask_id"]))
                    )
                except Exception:
                    pass
        await db.update_subtask(
            row["subtask_id"],
            status=TaskStatus.CANCELLED,
            worker_name=None,
            execution_token="",
            failure_category="replanned",
            error_message=f"Cancelled by replan: {reason}"[:500],
        )
        cancelled += 1
    return cancelled


async def _create_replan_subtasks(
    task_id: str, task_prompt: str, raw_subtasks: list
) -> tuple[int, str]:
    if not isinstance(raw_subtasks, list) or not raw_subtasks:
        return 0, "No subtasks supplied"

    existing = await db.get_subtasks_for_task(task_id)
    existing_ids = {row["subtask_id"] for row in existing}
    existing_graph: dict[str, set[str]] = {}
    for row in existing:
        try:
            deps = {
                _normalize_dep_id(task_id, str(dep))
                for dep in json.loads(row.get("depends_on") or "[]")
                if str(dep).strip()
            }
        except Exception:
            deps = set()
        existing_graph[row["subtask_id"]] = deps

    planned: list[dict] = []
    planned_ids: set[str] = set()
    for idx, item in enumerate(raw_subtasks, start=1):
        if not isinstance(item, dict):
            continue
        description = str(item.get("description", "")).strip()
        if not description:
            continue
        suggested = str(item.get("id", "")).strip() or f"rp{idx}-{new_id()[:4]}"
        subtask_id = _normalize_dep_id(task_id, suggested)
        if subtask_id in existing_ids or subtask_id in planned_ids:
            subtask_id = _normalize_dep_id(task_id, f"rp{idx}-{new_id()[:6]}")
        raw_deps = item.get("depends_on", [])
        if not isinstance(raw_deps, list):
            raw_deps = []
        deps = [_normalize_dep_id(task_id, str(dep)) for dep in raw_deps if str(dep).strip()]
        if subtask_id in deps:
            return 0, f"Invalid self dependency in {subtask_id}"
        planned.append(
            {
                "subtask_id": subtask_id,
                "description": description,
                "depends_on": deps,
            }
        )
        planned_ids.add(subtask_id)

    if not planned:
        return 0, "No valid subtasks after validation"

    valid_targets = existing_ids | planned_ids
    for spec in planned:
        missing = [dep for dep in spec["depends_on"] if dep not in valid_targets]
        if missing:
            return 0, f"Unknown dependency reference(s): {', '.join(missing[:3])}"

    dep_graph = dict(existing_graph)
    for spec in planned:
        dep_graph[spec["subtask_id"]] = set(spec["depends_on"])
    if _has_dependency_cycle(dep_graph):
        return 0, "Dependency cycle detected in replanned subtasks"

    for spec in planned:
        worker_prompt, skill_meta = build_worker_subtask_prompt(
            task_prompt=task_prompt,
            subtask_prompt=spec["description"],
            repo_root=workdir,
        )
        await db.create_subtask(
            subtask_id=spec["subtask_id"],
            task_id=task_id,
            prompt=worker_prompt,
            branch=f"task/{spec['subtask_id']}",
            depends_on=spec["depends_on"],
        )
        await db.add_task_event(
            task_id,
            "skill_injection",
            {
                "subtask_id": spec["subtask_id"],
                "packs": skill_meta.get("selected_packs", []),
                "skills": skill_meta.get("selected_skills", []),
                "profile": skill_meta.get("profile", "default"),
            },
        )
    return len(planned), ""


async def _apply_replan_plan(task_id: str, task_prompt: str, plan: dict) -> None:
    action = str(plan.get("action", "continue"))
    reason = str(plan.get("reason", "No reason provided")).strip() or "No reason provided"
    created = 0
    cancelled = 0

    if action == "cancel_remaining":
        cancelled = await _cancel_remaining_subtasks(task_id, reason)
    elif action == "add_subtasks":
        created, error = await _create_replan_subtasks(
            task_id, task_prompt, plan.get("subtasks", [])
        )
        if error:
            action = "continue"
            reason = f"Replan ignored: {error}"
    elif action == "replace_remaining":
        cancelled = await _cancel_remaining_subtasks(task_id, reason)
        created, error = await _create_replan_subtasks(
            task_id, task_prompt, plan.get("subtasks", [])
        )
        if error:
            action = "cancel_remaining"
            reason = f"{reason}. Could not add replacement subtasks: {error}"
    elif action != "continue":
        action = "continue"
        reason = f"Unsupported replan action: {plan.get('action')!r}"

    await _save_checkpoint(
        task_id,
        "replan_applied",
        {
            "action": action,
            "reason": reason[:500],
            "created_subtasks": created,
            "cancelled_subtasks": cancelled,
        },
    )
    await _broadcast_to_clients(
        serialize(
            ProgressMsg(
                task_id=task_id,
                output=(
                    f"Replan action={action}; reason={reason}; "
                    f"created={created}; cancelled={cancelled}"
                ),
            )
        )
    )

    assigned = await scheduler.try_assign_ready_subtasks_with_limit(task_id, max_assign=1)
    if assigned:
        _metric_inc("subtasks_assigned_total", assigned)


async def _maybe_replan_task(task_id: str) -> None:
    if PLANNING_INTERVAL <= 0:
        return
    task = await db.get_task(task_id)
    if not task or task["status"] in (
        TaskStatus.CANCELLED,
        TaskStatus.PAUSED,
        TaskStatus.FAILED,
        TaskStatus.COMPLETED,
    ):
        return

    _task_completion_counts[task_id] = _task_completion_counts.get(task_id, 0) + 1
    if _task_completion_counts[task_id] % PLANNING_INTERVAL != 0:
        return

    subtasks = await db.get_subtasks_for_task(task_id)
    completed = [s for s in subtasks if s["status"] == TaskStatus.COMPLETED]
    remaining = [
        s
        for s in subtasks
        if s["status"] in (TaskStatus.PENDING, TaskStatus.RUNNING)
    ]
    if not remaining:
        return

    plan = await replan_check(
        original_prompt=task["prompt"],
        completed_subtasks=completed,
        remaining_subtasks=remaining,
        workdir=workdir,
    )
    await _apply_replan_plan(task_id, task["prompt"], plan)
    if await scheduler.all_subtasks_done(task_id):
        async with _finalize_lock:
            if task_id in _finalizing_tasks:
                return
            _finalizing_tasks.add(task_id)
        try:
            await _finalize_task(task_id)
        finally:
            async with _finalize_lock:
                _finalizing_tasks.discard(task_id)


def _build_task_review(
    task_id: str,
    completed: list[dict],
    failed: list[dict],
    blocked: list[dict],
    cancelled: list[dict],
    merge_errors: list[str],
    test_ok: bool,
    test_output: str,
) -> dict:
    failed_subtasks = [
        {
            "subtask_id": s["subtask_id"],
            "category": s.get("failure_category", "execution_error"),
            "error": s.get("error_message", ""),
            "attempt_count": s.get("attempt_count", 0),
        }
        for s in failed
    ]
    blocked_subtasks = [
        {
            "subtask_id": s["subtask_id"],
            "category": s.get("failure_category", "dependency_failed"),
            "error": s.get("error_message", ""),
        }
        for s in blocked
    ]
    for s in cancelled:
        failed_subtasks.append(
            {
                "subtask_id": s["subtask_id"],
                "category": "cancelled",
                "error": s.get("error_message", "Cancelled by user"),
                "attempt_count": s.get("attempt_count", 0),
            }
        )

    summary_parts = []
    if merge_errors:
        summary_parts.append(
            f"merge had conflicts on {len(merge_errors)} branch(es)"
        )
    if failed_subtasks:
        summary_parts.append(
            f"{len(failed_subtasks)} subtask(s) failed or were cancelled"
        )
    if blocked_subtasks:
        summary_parts.append(
            f"{len(blocked_subtasks)} subtask(s) were blocked by dependencies"
        )
    if not test_ok:
        summary_parts.append("tests failed or were skipped")
    if not summary_parts:
        summary_parts.append("completion criteria were not met")

    return {
        "summary": (
            "Partial completion: "
            + "; ".join(summary_parts)
            + ". See task_review for failure reasons and required actions."
        ),
        "completed_count": len(completed),
        "failed_count": len(failed_subtasks),
        "blocked_count": len(blocked_subtasks),
        "failed_subtasks": failed_subtasks,
        "blocked_subtasks": blocked_subtasks,
        "test_summary": (
            "Tests passed"
            if test_ok
            else f"Tests failed/skipped: {test_output[:500]}"
        ),
    }


async def _apply_validation_gates(
    task_id: str,
    completed_subtasks: list[dict],
    has_remote_configured: bool,
) -> list[tuple[str, str]]:
    invalid: list[tuple[str, str]] = []
    for subtask in completed_subtasks:
        ok, reason = await validate_completed_subtask(
            workdir=workdir,
            branch=subtask["branch"],
            commit_sha=subtask.get("commit_sha", ""),
            has_remote=has_remote_configured,
        )
        if ok:
            continue

        sid = subtask["subtask_id"]
        invalid.append((sid, reason))
        _metric_inc("validation_failures_total")
        await db.update_subtask(
            sid,
            status=TaskStatus.FAILED,
            failure_category="validation_failed",
            error_message=reason,
        )
        await db.cascade_failure(task_id, sid)
    return invalid


async def _isolate_failed_merge_branches(
    task_id: str, failed_merge_map: dict[str, str]
) -> list[str]:
    """Mark merge-conflicted subtasks as failed so task can salvage others."""
    isolated: list[str] = []
    subtasks = await db.get_subtasks_for_task(task_id)
    by_branch = {s["branch"]: s for s in subtasks}
    for branch, error in failed_merge_map.items():
        subtask = by_branch.get(branch)
        if not subtask:
            continue
        sid = subtask["subtask_id"]
        if subtask["status"] not in (TaskStatus.COMPLETED, TaskStatus.RUNNING):
            continue
        await db.update_subtask(
            sid,
            status=TaskStatus.FAILED,
            failure_category="merge_conflict",
            error_message=f"Merge conflict while integrating branch {branch}: {error}",
        )
        _metric_inc("merge_conflicts_total")
        await db.cascade_failure(task_id, sid)
        isolated.append(sid)
    return isolated


async def _save_checkpoint(task_id: str, step: str, extra: dict) -> None:
    if not db:
        return
    try:
        task = await db.get_task(task_id)
        if not task:
            return
        subtasks = await db.get_subtasks_for_task(task_id)
        snapshot = {
            "task_id": task_id,
            "task_status": task["status"],
            "summary": task.get("summary", ""),
            "subtasks": [
                {
                    "subtask_id": s["subtask_id"],
                    "status": s["status"],
                    "worker_name": s.get("worker_name"),
                    "attempt_count": s.get("attempt_count", 0),
                    "failure_category": s.get("failure_category", ""),
                }
                for s in subtasks
            ],
            "extra": extra,
        }
        await db.save_checkpoint(task_id, step, snapshot)
        await db.add_task_event(
            task_id,
            step,
            {
                "task_status": task["status"],
                "extra": extra,
            },
        )
    except Exception as e:
        log.warning(
            "Failed to write checkpoint for %s at step %s: %s",
            task_id,
            step,
            e,
        )


async def _save_checkpoint_for_subtask(
    subtask_id: str, step: str, extra: dict
) -> None:
    subtask = await db.get_subtask(subtask_id)
    if not subtask:
        return
    await _save_checkpoint(subtask["task_id"], step, extra)


# --- Helpers ---


async def _dispatch_loop() -> None:
    while True:
        await asyncio.sleep(DISPATCH_INTERVAL)
        try:
            tasks = await db.get_dispatchable_tasks()
            if not tasks:
                continue
            # Fair dispatch: at most 1 subtask per task per cycle, priority-ordered.
            for task in tasks:
                assigned = await scheduler.try_assign_ready_subtasks_with_limit(
                    task["task_id"], max_assign=1
                )
                if assigned:
                    _metric_inc("subtasks_assigned_total", assigned)
                    await _save_checkpoint(
                        task["task_id"],
                        "subtask_assigned",
                        {"assigned_count": assigned, "source": "dispatch_loop"},
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("Dispatch loop error: %s", e, exc_info=True)


def _metric_inc(name: str, value: float = 1.0) -> None:
    _metrics[name] = _metrics.get(name, 0.0) + float(value)


async def _lease_watchdog_loop() -> None:
    while True:
        await asyncio.sleep(LEASE_SWEEP_INTERVAL)
        try:
            await _sweep_stale_leases_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("Lease watchdog error: %s", e, exc_info=True)


async def _sweep_stale_leases_once() -> int:
    stale = await db.get_stale_running_subtasks(SUBTASK_LEASE_TIMEOUT)
    if not stale:
        return 0
    reclaimed = 0
    for subtask in stale:
        sid = subtask["subtask_id"]
        worker_name = subtask.get("worker_name") or ""
        token = subtask.get("execution_token", "")
        log.warning(
            "Lease timeout on %s (worker=%s, token=%s, updated_at=%s)",
            sid,
            worker_name,
            token,
            subtask.get("updated_at"),
        )
        await scheduler.handle_task_error(
            worker_name,
            sid,
            "Lease timeout: no worker output/heartbeat",
            token,
        )
        _metric_inc("lease_timeouts_total")
        await _save_checkpoint_for_subtask(
            sid,
            "subtask_failed",
            {"reason": "lease_timeout"},
        )
        await _check_task_progress(sid)
        reclaimed += 1
    return reclaimed


def _is_authorized(ws: WebSocket) -> bool:
    if not SUPERGOD_AUTH_TOKEN:
        return True
    token = ws.query_params.get("token", "")
    if not token:
        # Fallback to raw query string parse.
        raw_qs = ws.scope.get("query_string", b"").decode()
        token = parse_qs(raw_qs).get("token", [""])[0]
    ok = token == SUPERGOD_AUTH_TOKEN
    if not ok:
        log.warning("Unauthorized websocket connection rejected")
    return ok


def _is_http_authorized(request: Request) -> bool:
    if not SUPERGOD_AUTH_TOKEN:
        return True
    token = request.query_params.get("token", "")
    if not token:
        return False
    return token == SUPERGOD_AUTH_TOKEN


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
        if ws in client_connections:
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

    global workdir, db_path
    workdir = args.workdir
    db_path = args.db

    import supergod.shared.config as cfg
    cfg.DB_PATH = args.db

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
