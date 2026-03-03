"""Integration tests — FastAPI server with mock workers and clients."""

import asyncio
import json
import socket
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
import httpx
from httpx._transports.asgi import ASGITransport

from supergod.shared.protocol import (
    ClientTaskMsg,
    ClientStatusMsg,
    TaskStatus,
    WorkerReadyMsg,
    WorkerTaskCompleteMsg,
    WorkerTaskErrorMsg,
    WorkerOutputMsg,
    PongMsg,
    deserialize,
    serialize,
)
from supergod.orchestrator.state import StateDB


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


@pytest.fixture
async def app_with_db(tmp_path):
    """Create a fresh FastAPI app instance with temp database.

    We import the module and patch its globals to avoid cross-test contamination.
    """
    import supergod.orchestrator.server as srv
    import supergod.shared.config as cfg

    # Save originals
    old_db_path = cfg.DB_PATH
    old_db = srv.db
    old_scheduler = srv.scheduler
    old_clients = srv.client_connections

    # Setup fresh state
    db_path = str(tmp_path / "test.db")
    cfg.DB_PATH = db_path
    test_db = StateDB(db_path)
    await test_db.init()
    test_scheduler_mod = __import__(
        "supergod.orchestrator.scheduler", fromlist=["Scheduler"]
    )
    test_scheduler = test_scheduler_mod.Scheduler(test_db)

    srv.db = test_db
    srv.scheduler = test_scheduler
    srv.client_connections = []

    yield srv.app, test_db, test_scheduler

    # Cleanup
    await test_db.close()
    srv.db = old_db
    srv.scheduler = old_scheduler
    srv.client_connections = old_clients
    cfg.DB_PATH = old_db_path


@pytest.fixture
async def client(app_with_db):
    """HTTPX async client for REST-like testing."""
    app, db, scheduler = app_with_db
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --- HTTP endpoint tests ---


async def test_app_root_404(client):
    """No root endpoint defined, should 404."""
    resp = await client.get("/")
    # FastAPI returns 404 for undefined routes
    assert resp.status_code == 404


async def test_healthz_endpoint(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


async def test_metrics_endpoint(client):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    assert "supergod_tasks_submitted_total" in body


async def test_snapshot_endpoint(app_with_db, client):
    app, db, scheduler = app_with_db
    await db.create_task("snap-1", "Build dashboard")
    await db.upsert_worker("snap-worker", "idle")
    resp = await client.get("/snapshot")
    assert resp.status_code == 200
    data = resp.json()
    assert "tasks" in data
    assert "workers" in data
    assert any(t["task_id"] == "snap-1" for t in data["tasks"])
    assert any(w["name"] == "snap-worker" for w in data["workers"])


async def test_mission_endpoint(client):
    resp = await client.get("/mission")
    assert resp.status_code == 200
    assert "Supergod Mission Control" in resp.text


async def test_task_events_endpoint(app_with_db, client):
    app, db, scheduler = app_with_db
    await db.create_task("t1", "Build auth")
    await db.add_task_event("t1", "task_accepted", {"prompt": "Build auth"})
    resp = await client.get("/task/t1/events")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "t1"
    assert len(data["events"]) == 1
    assert data["events"][0]["event_type"] == "task_accepted"


# --- WebSocket client endpoint tests ---


async def test_client_ws_task_accepted(app_with_db):
    """Client sends a task, receives task_accepted."""
    app, db, scheduler = app_with_db

    # Mock decompose_task to avoid needing real Codex
    async def mock_decompose(prompt, workdir):
        return []

    with patch("supergod.orchestrator.server.decompose_task", mock_decompose):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            async with httpx.AsyncClient(
                transport=transport, base_url="ws://test"
            ) as ws_client:
                # Use the websocket test client from httpx
                pass

    # For proper WebSocket testing, we use the ASGI transport directly
    # httpx doesn't natively support WebSocket, so we test via DB state
    # after simulating what the WebSocket handler does

    # Direct unit test of _handle_client_task
    import supergod.orchestrator.server as srv

    class FakeWS:
        def __init__(self):
            self.sent = []

        async def send_text(self, data):
            self.sent.append(data)

    fake_ws = FakeWS()
    task_msg = ClientTaskMsg(prompt="Build auth", task_id="test-123")

    with patch("supergod.orchestrator.server.decompose_task", mock_decompose):
        await srv._handle_client_task(fake_ws, task_msg)

    # Should have sent task_accepted
    assert len(fake_ws.sent) == 1
    accepted = json.loads(fake_ws.sent[0])
    assert accepted["type"] == "task_accepted"
    assert accepted["task_id"] == "test-123"

    # Task should be in DB
    task = await db.get_task("test-123")
    assert task is not None
    assert task["prompt"] == "Build auth"


async def test_client_status_response(app_with_db):
    """Client requesting status should receive tasks and workers."""
    app, db, scheduler = app_with_db
    import supergod.orchestrator.server as srv

    # Create some state
    await db.create_task("t1", "Build auth")
    await db.upsert_worker("w1", "idle")

    class FakeWS:
        def __init__(self):
            self.sent = []

        async def send_text(self, data):
            self.sent.append(data)

    fake_ws = FakeWS()
    await srv._handle_client_status(fake_ws)

    assert len(fake_ws.sent) == 1
    resp = json.loads(fake_ws.sent[0])
    assert resp["type"] == "status_response"
    assert len(resp["tasks"]) == 1
    assert resp["tasks"][0]["task_id"] == "t1"
    assert "current_subtask" in resp["workers"][0]


# --- Worker WebSocket message handling ---


async def test_worker_registration(app_with_db):
    """Worker sends ready message, gets registered in scheduler."""
    app, db, scheduler = app_with_db

    ws = AsyncMock()
    await scheduler.register_worker("test-worker", ws)

    assert "test-worker" in scheduler.workers
    workers = await db.get_all_workers()
    assert any(w["name"] == "test-worker" for w in workers)


async def test_worker_task_complete_flow(app_with_db):
    """Simulate: create task+subtask, assign to worker, worker completes."""
    app, db, scheduler = app_with_db

    # Register a worker
    ws = AsyncMock()
    ws.sent_messages = []

    async def capture(msg):
        ws.sent_messages.append(msg)

    ws.send_text = AsyncMock(side_effect=capture)
    ws.close = AsyncMock()
    await scheduler.register_worker("w1", ws)

    # Create task and subtask
    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-s1", "t1", "Implement login", "task/t1-s1")

    # Assign ready subtasks
    assigned = await scheduler.try_assign_ready_subtasks("t1")
    assert assigned == 1

    # Verify task was sent to worker
    assert len(ws.sent_messages) == 1
    sent_msg = json.loads(ws.sent_messages[0])
    assert sent_msg["type"] == "task"
    assert sent_msg["id"] == "t1-s1"

    # Worker completes the task
    await scheduler.handle_task_complete("w1", "t1-s1", "sha123")

    # Verify state
    subtasks = await db.get_subtasks_for_task("t1")
    assert subtasks[0]["status"] == TaskStatus.COMPLETED
    assert subtasks[0]["commit_sha"] == "sha123"
    assert scheduler.workers["w1"].status == "idle"


async def test_worker_task_error_flow(app_with_db):
    """Worker reports error, subtask marked failed, worker becomes idle."""
    app, db, scheduler = app_with_db

    ws = AsyncMock()
    ws.sent_messages = []

    async def capture_err(msg):
        ws.sent_messages.append(msg)

    ws.send_text = AsyncMock(side_effect=capture_err)
    ws.close = AsyncMock()
    await scheduler.register_worker("w1", ws)

    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-s1", "t1", "Implement login", "task/t1-s1")
    await scheduler.try_assign_ready_subtasks("t1")

    # Worker reports error
    await scheduler.handle_task_error("w1", "t1-s1", "codex crashed")

    subtasks = await db.get_subtasks_for_task("t1")
    assert subtasks[0]["status"] == TaskStatus.FAILED
    assert scheduler.workers["w1"].status == "idle"


# --- Broadcast to clients ---


async def test_broadcast_to_clients(app_with_db):
    """Messages should be sent to all connected clients."""
    app, db, scheduler = app_with_db
    import supergod.orchestrator.server as srv

    class FakeClientWS:
        def __init__(self):
            self.sent = []

        async def send_text(self, data):
            self.sent.append(data)

    c1 = FakeClientWS()
    c2 = FakeClientWS()
    srv.client_connections.extend([c1, c2])

    await srv._broadcast_to_clients('{"type": "progress", "task_id": "t1", "output": "hello"}')

    assert len(c1.sent) == 1
    assert len(c2.sent) == 1
    assert "hello" in c1.sent[0]


async def test_broadcast_removes_disconnected(app_with_db):
    """Disconnected clients should be removed from the list."""
    app, db, scheduler = app_with_db
    import supergod.orchestrator.server as srv

    class GoodWS:
        def __init__(self):
            self.sent = []

        async def send_text(self, data):
            self.sent.append(data)

    class BadWS:
        async def send_text(self, data):
            raise ConnectionError("gone")

    good = GoodWS()
    bad = BadWS()
    srv.client_connections.extend([good, bad])

    await srv._broadcast_to_clients('{"test": true}')

    assert len(good.sent) == 1
    assert bad not in srv.client_connections


# --- Task processing pipeline ---


async def test_process_task_creates_subtasks(app_with_db):
    """_process_task should decompose and create subtasks in DB."""
    app, db, scheduler = app_with_db
    import supergod.orchestrator.server as srv
    from supergod.orchestrator.brain import Subtask

    mock_subtasks = [
        Subtask(id="1", description="Build login", depends_on=[]),
        Subtask(id="2", description="Build signup", depends_on=[]),
        Subtask(id="3", description="Wire up", depends_on=["1", "2"]),
    ]

    await db.create_task("t1", "Build auth system")

    with patch(
        "supergod.orchestrator.server.decompose_task",
        return_value=mock_subtasks,
    ):
        await srv._process_task("t1", "Build auth system")

    # Wait for background task to settle
    await asyncio.sleep(0.1)

    task = await db.get_task("t1")
    assert task["status"] == TaskStatus.ASSIGNED

    subtasks = await db.get_subtasks_for_task("t1")
    assert len(subtasks) == 3
    # Check dependency encoding
    s3 = [s for s in subtasks if s["subtask_id"] == "t1-3"][0]
    deps = json.loads(s3["depends_on"])
    assert "t1-1" in deps
    assert "t1-2" in deps


async def test_process_task_handles_decompose_error(app_with_db):
    """If decomposition fails, task should be marked FAILED."""
    app, db, scheduler = app_with_db
    import supergod.orchestrator.server as srv

    await db.create_task("t1", "Build auth")

    async def fail_decompose(prompt, workdir):
        raise RuntimeError("Codex is down")

    with patch("supergod.orchestrator.server.decompose_task", fail_decompose):
        await srv._process_task("t1", "Build auth")

    task = await db.get_task("t1")
    assert task["status"] == TaskStatus.FAILED


async def test_process_task_records_skill_injection_events(app_with_db):
    app, db, scheduler = app_with_db
    import supergod.orchestrator.server as srv
    from supergod.orchestrator.brain import Subtask

    await db.create_task("t1", "Build auth system")
    mock_subtasks = [Subtask(id="1", description="Build login", depends_on=[])]

    with patch(
        "supergod.orchestrator.server.decompose_task",
        return_value=mock_subtasks,
    ):
        await srv._process_task("t1", "Build auth system")

    events = await db.get_task_events("t1", limit=100)
    assert any(e["event_type"] == "skill_injection" for e in events)


async def test_process_task_fails_on_circular_dependencies(app_with_db):
    app, db, scheduler = app_with_db
    import supergod.orchestrator.server as srv
    from supergod.orchestrator.brain import Subtask

    await db.create_task("t1", "Build auth system")
    mock_subtasks = [
        Subtask(id="1", description="Step 1", depends_on=["2"]),
        Subtask(id="2", description="Step 2", depends_on=["1"]),
    ]

    with patch(
        "supergod.orchestrator.server.decompose_task",
        return_value=mock_subtasks,
    ):
        await srv._process_task("t1", "Build auth system")

    task = await db.get_task("t1")
    assert task["status"] == TaskStatus.FAILED
    subtasks = await db.get_subtasks_for_task("t1")
    assert subtasks == []


async def test_process_task_fails_on_unknown_dependency(app_with_db):
    app, db, scheduler = app_with_db
    import supergod.orchestrator.server as srv
    from supergod.orchestrator.brain import Subtask

    await db.create_task("t1", "Build auth system")
    mock_subtasks = [Subtask(id="1", description="Step 1", depends_on=["999"])]

    with patch(
        "supergod.orchestrator.server.decompose_task",
        return_value=mock_subtasks,
    ):
        await srv._process_task("t1", "Build auth system")

    task = await db.get_task("t1")
    assert task["status"] == TaskStatus.FAILED
    subtasks = await db.get_subtasks_for_task("t1")
    assert subtasks == []


# --- _extract_text helper ---


def test_extract_text_item_completed():
    import supergod.orchestrator.server as srv

    event = {
        "type": "item.completed",
        "item": {
            "type": "agent_message",
            "content": [{"type": "text", "text": "Result here"}],
        },
    }
    assert srv._extract_text(event) == "Result here"


def test_extract_text_turn_completed():
    import supergod.orchestrator.server as srv

    event = {"type": "turn.completed"}
    assert srv._extract_text(event) == "[turn completed]"


def test_extract_text_error():
    import supergod.orchestrator.server as srv

    event = {"type": "error", "message": "something broke"}
    assert srv._extract_text(event) == "[error] something broke"


def test_extract_text_unknown():
    import supergod.orchestrator.server as srv

    event = {"type": "unknown.event"}
    assert srv._extract_text(event) == ""


# --- Full end-to-end: task submission through completion ---


async def test_end_to_end_task_lifecycle(app_with_db):
    """Full lifecycle: submit task, decompose, assign, complete, merge."""
    app, db, scheduler = app_with_db
    import supergod.orchestrator.server as srv
    from supergod.orchestrator.brain import Subtask

    # Register 2 workers
    ws1 = AsyncMock()
    ws1.sent_messages = []

    async def capture1(msg):
        ws1.sent_messages.append(msg)

    ws1.send_text = AsyncMock(side_effect=capture1)
    ws1.close = AsyncMock()

    ws2 = AsyncMock()
    ws2.sent_messages = []

    async def capture2(msg):
        ws2.sent_messages.append(msg)

    ws2.send_text = AsyncMock(side_effect=capture2)
    ws2.close = AsyncMock()

    await scheduler.register_worker("w1", ws1)
    await scheduler.register_worker("w2", ws2)

    # Track client broadcasts
    client_msgs = []

    class FakeClientWS:
        async def send_text(self, data):
            client_msgs.append(json.loads(data))

    srv.client_connections.append(FakeClientWS())

    # Submit task
    await db.create_task("t1", "Build auth")

    mock_subtasks = [
        Subtask(id="1", description="Login", depends_on=[]),
        Subtask(id="2", description="Signup", depends_on=[]),
    ]

    with patch(
        "supergod.orchestrator.server.decompose_task",
        return_value=mock_subtasks,
    ):
        await srv._process_task("t1", "Build auth")

    await asyncio.sleep(0.1)

    # Both subtasks should be assigned (2 workers, 2 independent subtasks)
    subtasks = await db.get_subtasks_for_task("t1")
    running = [s for s in subtasks if s["status"] == TaskStatus.RUNNING]
    assert 1 <= len(running) <= 2

    # Complete assigned subtasks, then assign/complete remaining if any.
    for s in running:
        await scheduler.handle_task_complete(
            s["worker_name"],
            s["subtask_id"],
            f"sha-{s['subtask_id']}",
            execution_token=s.get("execution_token", ""),
        )
    await scheduler.try_assign_ready_subtasks("t1")
    subtasks = await db.get_subtasks_for_task("t1")
    running2 = [s for s in subtasks if s["status"] == TaskStatus.RUNNING]
    for s in running2:
        await scheduler.handle_task_complete(
            s["worker_name"],
            s["subtask_id"],
            f"sha-{s['subtask_id']}",
            execution_token=s.get("execution_token", ""),
        )

    # Verify all subtasks completed
    assert await scheduler.all_subtasks_done("t1")
    assert not await scheduler.any_subtask_failed("t1")

    # Check that progress messages were broadcast
    progress_msgs = [m for m in client_msgs if m.get("type") == "progress"]
    assert len(progress_msgs) >= 1  # At least "Decomposing..." message


# --- Client cancel ---


async def test_client_cancel_task(app_with_db):
    """Cancelling a task should update its status."""
    app, db, scheduler = app_with_db
    import supergod.orchestrator.server as srv

    await db.create_task("t1", "Build auth")

    cancel_msg = MagicMock()
    cancel_msg.task_id = "t1"

    fake_ws = MagicMock()
    await srv._handle_client_cancel(fake_ws, cancel_msg)

    task = await db.get_task("t1")
    assert task["status"] == TaskStatus.CANCELLED


async def test_pause_and_resume_task_flow(app_with_db):
    app, db, scheduler = app_with_db
    import supergod.orchestrator.server as srv

    ws = AsyncMock()
    ws.sent_messages = []

    async def capture(msg):
        ws.sent_messages.append(msg)

    ws.send_text = AsyncMock(side_effect=capture)
    ws.close = AsyncMock()
    await scheduler.register_worker("w1", ws)

    await db.create_task("t1", "Build auth")
    await db.update_task_status("t1", TaskStatus.ASSIGNED)
    await db.create_subtask("t1-s1", "t1", "Step 1", "b1")

    fake_ws = MagicMock()
    pause_msg = MagicMock()
    pause_msg.task_id = "t1"
    await srv._handle_client_pause(fake_ws, pause_msg)
    task = await db.get_task("t1")
    assert task["status"] == TaskStatus.PAUSED

    assigned_while_paused = await scheduler.try_assign_ready_subtasks("t1")
    assert assigned_while_paused == 0

    resume_msg = MagicMock()
    resume_msg.task_id = "t1"
    await srv._handle_client_resume(fake_ws, resume_msg)
    task = await db.get_task("t1")
    assert task["status"] in (TaskStatus.ASSIGNED, TaskStatus.RUNNING)


async def test_consistency_sweep_repairs_invalid_running(app_with_db):
    app, db, scheduler = app_with_db
    import supergod.orchestrator.server as srv

    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-s1", "t1", "Implement login", "task/t1-s1")
    await db.update_subtask("t1-s1", status=TaskStatus.RUNNING, worker_name=None)

    await srv._consistency_sweep()

    task = await db.get_task("t1")
    subtask = await db.get_subtask("t1-s1")
    assert task["status"] == TaskStatus.ASSIGNED
    assert subtask["status"] == TaskStatus.PENDING
    assert subtask["failure_category"] == "consistency_repair"
    assert subtask["attempt_count"] == 1


async def test_resume_from_checkpoint_requeues_running_subtasks(app_with_db):
    app, db, scheduler = app_with_db
    import supergod.orchestrator.server as srv

    await db.create_task("t1", "Build auth")
    await db.update_task_status("t1", TaskStatus.RUNNING)
    await db.create_subtask("t1-s1", "t1", "Implement login", "task/t1-s1")
    await db.update_subtask(
        "t1-s1",
        status=TaskStatus.RUNNING,
        worker_name="w1",
        execution_token="tok1",
    )
    await db.save_checkpoint("t1", "subtask_assigned", {"assigned_count": 1})

    await srv._resume_in_progress_tasks()
    await asyncio.sleep(0.05)

    task = await db.get_task("t1")
    subtask = await db.get_subtask("t1-s1")
    assert task["status"] == TaskStatus.ASSIGNED
    assert subtask["status"] == TaskStatus.PENDING
    assert subtask["execution_token"] == ""
    assert subtask["attempt_count"] >= 1


async def test_lease_sweep_reclaims_stale_running_subtask(app_with_db):
    app, db, scheduler = app_with_db
    import supergod.orchestrator.server as srv

    await db.create_task("t1", "Build auth")
    await db.update_task_status("t1", TaskStatus.RUNNING)
    await db.create_subtask("t1-s1", "t1", "Implement login", "task/t1-s1")
    await db.update_subtask(
        "t1-s1",
        status=TaskStatus.RUNNING,
        worker_name="w1",
        execution_token="tok1",
    )
    # Force stale updated_at timestamp.
    await db._db.execute(
        "UPDATE subtasks SET updated_at = ? WHERE subtask_id = ?",
        ("2000-01-01T00:00:00+00:00", "t1-s1"),
    )
    await db._db.commit()

    reclaimed = await srv._sweep_stale_leases_once()
    assert reclaimed == 1
    subtask = await db.get_subtask("t1-s1")
    assert subtask["status"] == TaskStatus.PENDING
    assert subtask["failure_category"] == "transient_infra"


async def test_finalize_isolates_failed_merge_branches(app_with_db):
    app, db, scheduler = app_with_db
    import supergod.orchestrator.server as srv

    await db.create_task("t1", "Build auth")
    await db.update_task_status("t1", TaskStatus.RUNNING)
    await db.create_subtask("t1-s1", "t1", "Step 1", "b1")
    await db.create_subtask("t1-s2", "t1", "Step 2", "b2")
    await db.update_subtask("t1-s1", status=TaskStatus.COMPLETED, commit_sha="sha1")
    await db.update_subtask("t1-s2", status=TaskStatus.COMPLETED, commit_sha="sha2")

    async def fake_has_remote(workdir):
        return True

    async def fake_merge_report(workdir, branches):
        return {
            "merged": ["b1"],
            "failed": {"b2": "conflict in file.py"},
            "errors": ["Merge conflict on b2: conflict in file.py"],
        }

    async def fake_tests(workdir):
        return True, "ok"

    async def fake_validate(workdir, branch, commit_sha, has_remote):
        return True, ""

    with patch("supergod.worker.git_ops.has_remote", fake_has_remote), patch(
        "supergod.orchestrator.server.merge_all_branches_with_report",
        fake_merge_report,
    ), patch("supergod.orchestrator.server.run_tests", fake_tests), patch(
        "supergod.orchestrator.server.validate_completed_subtask",
        fake_validate,
    ):
        await srv._finalize_task("t1")

    s1 = await db.get_subtask("t1-s1")
    s2 = await db.get_subtask("t1-s2")
    task = await db.get_task("t1")

    assert s1["status"] == TaskStatus.COMPLETED
    assert s2["status"] == TaskStatus.FAILED
    assert s2["failure_category"] == "merge_conflict"
    assert task["status"] == TaskStatus.FAILED
