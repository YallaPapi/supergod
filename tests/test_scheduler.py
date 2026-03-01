"""Tests for the Scheduler — worker tracking and subtask assignment."""

import asyncio
import json
from typing import List
from unittest.mock import AsyncMock, MagicMock

import pytest

from supergod.shared.protocol import TaskStatus, WorkerStatus
from supergod.orchestrator.scheduler import Scheduler, WorkerConnection
from supergod.orchestrator.state import StateDB


@pytest.fixture
async def db(tmp_path):
    """Create a fresh StateDB for each test."""
    state = StateDB(str(tmp_path / "test.db"))
    await state.init()
    yield state
    await state.close()


@pytest.fixture
def scheduler(db: StateDB):
    return Scheduler(db)


def make_mock_ws():
    """Create a mock WebSocket that records sent messages via send_text()."""
    ws = AsyncMock()
    ws.sent_messages = []

    original_send_text = ws.send_text

    async def capture_send_text(msg):
        ws.sent_messages.append(msg)

    ws.send_text = AsyncMock(side_effect=capture_send_text)
    ws.close = AsyncMock()
    return ws


# --- Worker registration ---


async def test_register_worker(scheduler: Scheduler, db: StateDB):
    ws = make_mock_ws()
    await scheduler.register_worker("w1", ws)
    assert "w1" in scheduler.workers
    assert scheduler.workers["w1"].status == WorkerStatus.IDLE

    # Also persisted in DB
    workers = await db.get_all_workers()
    assert len(workers) == 1
    assert workers[0]["name"] == "w1"
    assert workers[0]["status"] == WorkerStatus.IDLE


async def test_unregister_worker(scheduler: Scheduler, db: StateDB):
    ws = make_mock_ws()
    await scheduler.register_worker("w1", ws)
    await scheduler.unregister_worker("w1")
    assert "w1" not in scheduler.workers

    workers = await db.get_all_workers()
    assert workers[0]["status"] == WorkerStatus.OFFLINE


async def test_unregister_nonexistent_worker(scheduler: Scheduler):
    """Unregistering a worker that was never registered should not crash."""
    await scheduler.unregister_worker("ghost")


async def test_get_idle_workers(scheduler: Scheduler):
    ws1 = make_mock_ws()
    ws2 = make_mock_ws()
    await scheduler.register_worker("w1", ws1)
    await scheduler.register_worker("w2", ws2)

    idle = scheduler.get_idle_workers()
    assert len(idle) == 2

    # Make one busy
    scheduler.workers["w1"].status = WorkerStatus.BUSY
    idle = scheduler.get_idle_workers()
    assert len(idle) == 1
    assert idle[0].name == "w2"


# --- Task assignment ---


async def _setup_task_with_subtask(db: StateDB, task_id="t1", subtask_id="t1-s1"):
    """Helper: create a task with one pending subtask."""
    await db.create_task(task_id, "Build something")
    await db.create_subtask(subtask_id, task_id, "Do subtask", f"task/{subtask_id}")
    subtasks = await db.get_subtasks_for_task(task_id)
    return subtasks[0]


async def test_assign_subtask(scheduler: Scheduler, db: StateDB):
    ws = make_mock_ws()
    await scheduler.register_worker("w1", ws)
    subtask = await _setup_task_with_subtask(db)

    worker_name = await scheduler.assign_subtask(subtask, "t1")
    assert worker_name == "w1"

    # Worker should now be busy
    assert scheduler.workers["w1"].status == WorkerStatus.BUSY

    # DB should reflect the assignment
    subtasks = await db.get_subtasks_for_task("t1")
    assert subtasks[0]["status"] == TaskStatus.RUNNING
    assert subtasks[0]["worker_name"] == "w1"

    # A WorkerTaskMsg should have been sent via send_text
    assert len(ws.sent_messages) == 1
    sent = ws.sent_messages[0]
    assert '"task"' in sent
    assert "t1-s1" in sent


async def test_assign_subtask_no_idle_workers(scheduler: Scheduler, db: StateDB):
    """When no workers are idle, assign_subtask returns None."""
    subtask = await _setup_task_with_subtask(db)
    result = await scheduler.assign_subtask(subtask, "t1")
    assert result is None


async def test_no_double_assignment(scheduler: Scheduler, db: StateDB):
    """A busy worker should not receive a second task."""
    ws = make_mock_ws()
    await scheduler.register_worker("w1", ws)

    await db.create_task("t1", "Build something")
    await db.create_subtask("t1-s1", "t1", "Step 1", "b1")
    await db.create_subtask("t1-s2", "t1", "Step 2", "b2")

    subtasks = await db.get_subtasks_for_task("t1")
    # Assign first
    result1 = await scheduler.assign_subtask(subtasks[0], "t1")
    assert result1 == "w1"

    # Try to assign second — worker is busy
    result2 = await scheduler.assign_subtask(subtasks[1], "t1")
    assert result2 is None


# --- Task completion and error handling ---


async def test_handle_task_complete(scheduler: Scheduler, db: StateDB):
    ws = make_mock_ws()
    await scheduler.register_worker("w1", ws)
    subtask = await _setup_task_with_subtask(db)
    await scheduler.assign_subtask(subtask, "t1")

    await scheduler.handle_task_complete("w1", "t1-s1", "abc123")

    # Worker should be idle again
    assert scheduler.workers["w1"].status == WorkerStatus.IDLE

    # Subtask should be completed with commit
    subtasks = await db.get_subtasks_for_task("t1")
    assert subtasks[0]["status"] == TaskStatus.COMPLETED
    assert subtasks[0]["commit_sha"] == "abc123"

    # Worker should have no current subtask
    workers = await db.get_all_workers()
    assert workers[0]["current_subtask"] is None


async def test_handle_task_error(scheduler: Scheduler, db: StateDB):
    ws = make_mock_ws()
    await scheduler.register_worker("w1", ws)
    subtask = await _setup_task_with_subtask(db)
    await scheduler.assign_subtask(subtask, "t1")

    await scheduler.handle_task_error("w1", "t1-s1", "codex crashed")

    # Worker should be idle again
    assert scheduler.workers["w1"].status == WorkerStatus.IDLE

    # Subtask should be failed
    subtasks = await db.get_subtasks_for_task("t1")
    assert subtasks[0]["status"] == TaskStatus.FAILED


async def test_handle_task_error_retryable_transient(
    scheduler: Scheduler, db: StateDB
):
    ws = make_mock_ws()
    await scheduler.register_worker("w1", ws)
    subtask = await _setup_task_with_subtask(db)
    await scheduler.assign_subtask(subtask, "t1")

    await scheduler.handle_task_error(
        "w1", "t1-s1", "Connection timed out talking to orchestrator"
    )

    subtasks = await db.get_subtasks_for_task("t1")
    s = subtasks[0]
    assert s["status"] == TaskStatus.PENDING
    assert s["attempt_count"] == 1
    assert s["failure_category"] == "transient_infra"


async def test_handle_task_complete_unknown_worker(scheduler: Scheduler, db: StateDB):
    """Unknown completion without a lease/token should be ignored safely."""
    subtask = await _setup_task_with_subtask(db)
    # Worker not registered and no valid lease/token => ignore.
    await scheduler.handle_task_complete("ghost", "t1-s1", "abc")
    subtasks = await db.get_subtasks_for_task("t1")
    assert subtasks[0]["status"] == TaskStatus.PENDING


# --- try_assign_ready_subtasks ---


async def test_try_assign_ready_subtasks(scheduler: Scheduler, db: StateDB):
    ws1 = make_mock_ws()
    ws2 = make_mock_ws()
    await scheduler.register_worker("w1", ws1)
    await scheduler.register_worker("w2", ws2)

    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-s1", "t1", "Step 1", "b1")
    await db.create_subtask("t1-s2", "t1", "Step 2", "b2")

    assigned = await scheduler.try_assign_ready_subtasks("t1")
    assert assigned == 2

    # Both workers should be busy
    assert scheduler.workers["w1"].status == WorkerStatus.BUSY
    assert scheduler.workers["w2"].status == WorkerStatus.BUSY


async def test_try_assign_ready_subtasks_respects_deps(scheduler: Scheduler, db: StateDB):
    ws1 = make_mock_ws()
    await scheduler.register_worker("w1", ws1)

    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-s1", "t1", "Step 1", "b1")
    await db.create_subtask("t1-s2", "t1", "Step 2", "b2", depends_on=["t1-s1"])

    assigned = await scheduler.try_assign_ready_subtasks("t1")
    # Only s1 is ready (s2 depends on s1)
    assert assigned == 1


async def test_try_assign_limited_by_workers(scheduler: Scheduler, db: StateDB):
    ws = make_mock_ws()
    await scheduler.register_worker("w1", ws)

    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-s1", "t1", "Step 1", "b1")
    await db.create_subtask("t1-s2", "t1", "Step 2", "b2")
    await db.create_subtask("t1-s3", "t1", "Step 3", "b3")

    # Only 1 worker available, so only 1 assigned
    assigned = await scheduler.try_assign_ready_subtasks("t1")
    assert assigned == 1


# --- all_subtasks_done / any_subtask_failed ---


async def test_all_subtasks_done(scheduler: Scheduler, db: StateDB):
    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-s1", "t1", "Step 1", "b1")
    await db.create_subtask("t1-s2", "t1", "Step 2", "b2")

    assert not await scheduler.all_subtasks_done("t1")

    await db.update_subtask("t1-s1", status=TaskStatus.COMPLETED)
    assert not await scheduler.all_subtasks_done("t1")

    await db.update_subtask("t1-s2", status=TaskStatus.COMPLETED)
    assert await scheduler.all_subtasks_done("t1")


async def test_all_subtasks_done_includes_failed(scheduler: Scheduler, db: StateDB):
    """Failed subtasks also count as 'done' (not pending/running)."""
    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-s1", "t1", "Step 1", "b1")
    await db.create_subtask("t1-s2", "t1", "Step 2", "b2")

    await db.update_subtask("t1-s1", status=TaskStatus.COMPLETED)
    await db.update_subtask("t1-s2", status=TaskStatus.FAILED)
    assert await scheduler.all_subtasks_done("t1")


async def test_any_subtask_failed(scheduler: Scheduler, db: StateDB):
    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-s1", "t1", "Step 1", "b1")
    await db.create_subtask("t1-s2", "t1", "Step 2", "b2")

    assert not await scheduler.any_subtask_failed("t1")

    await db.update_subtask("t1-s1", status=TaskStatus.FAILED)
    assert await scheduler.any_subtask_failed("t1")


# --- Concurrent assignment safety ---


async def test_concurrent_assign_no_double_booking(scheduler: Scheduler, db: StateDB):
    """Two concurrent assign_subtask calls should not assign same worker twice."""
    ws = make_mock_ws()
    await scheduler.register_worker("w1", ws)

    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-s1", "t1", "Step 1", "b1")
    await db.create_subtask("t1-s2", "t1", "Step 2", "b2")

    subtasks = await db.get_subtasks_for_task("t1")

    # Race two assignments
    results = await asyncio.gather(
        scheduler.assign_subtask(subtasks[0], "t1"),
        scheduler.assign_subtask(subtasks[1], "t1"),
    )
    assigned = [r for r in results if r is not None]
    # Only one should succeed since there's only one worker
    assert len(assigned) == 1


async def test_stale_completion_ignored_by_execution_token(
    scheduler: Scheduler, db: StateDB
):
    ws = make_mock_ws()
    await scheduler.register_worker("w1", ws)
    subtask = await _setup_task_with_subtask(db)
    await scheduler.assign_subtask(subtask, "t1")
    current = (await db.get_subtasks_for_task("t1"))[0]
    assert current["execution_token"]

    await scheduler.handle_task_complete(
        "w1",
        "t1-s1",
        "abc123",
        execution_token="stale-token",
    )
    after = (await db.get_subtasks_for_task("t1"))[0]
    assert after["status"] == TaskStatus.RUNNING
    assert after["execution_token"] == current["execution_token"]


async def test_stale_error_ignored_by_execution_token(
    scheduler: Scheduler, db: StateDB
):
    ws = make_mock_ws()
    await scheduler.register_worker("w1", ws)
    subtask = await _setup_task_with_subtask(db)
    await scheduler.assign_subtask(subtask, "t1")
    current = (await db.get_subtasks_for_task("t1"))[0]
    assert current["execution_token"]

    await scheduler.handle_task_error(
        "w1",
        "t1-s1",
        "Worker disconnected",
        execution_token="stale-token",
    )
    after = (await db.get_subtasks_for_task("t1"))[0]
    assert after["status"] == TaskStatus.RUNNING
    assert after["execution_token"] == current["execution_token"]


async def test_unregister_worker_reclaims_running_subtask(
    scheduler: Scheduler, db: StateDB
):
    ws = make_mock_ws()
    await scheduler.register_worker("w1", ws)
    subtask = await _setup_task_with_subtask(db)
    await scheduler.assign_subtask(subtask, "t1")

    await scheduler.unregister_worker("w1")

    subtasks = await db.get_subtasks_for_task("t1")
    s = subtasks[0]
    assert s["status"] == TaskStatus.PENDING
    assert s["attempt_count"] == 1
    assert s["failure_category"] == "transient_infra"

    workers = await db.get_all_workers()
    assert workers[0]["status"] == WorkerStatus.OFFLINE


async def test_max_workers_per_task_cap(
    scheduler: Scheduler, db: StateDB
):
    ws1 = make_mock_ws()
    ws2 = make_mock_ws()
    await scheduler.register_worker("w1", ws1)
    await scheduler.register_worker("w2", ws2)

    await db.create_task("t1", "Build auth")
    await db.update_task_status("t1", TaskStatus.ASSIGNED)
    await db.create_subtask("t1-s1", "t1", "Step 1", "b1")
    await db.create_subtask("t1-s2", "t1", "Step 2", "b2")

    import supergod.orchestrator.scheduler as sched_mod
    old_cap = sched_mod.MAX_WORKERS_PER_TASK
    sched_mod.MAX_WORKERS_PER_TASK = 1
    try:
        assigned = await scheduler.try_assign_ready_subtasks_with_limit(
            "t1", max_assign=None
        )
        assert assigned == 1
    finally:
        sched_mod.MAX_WORKERS_PER_TASK = old_cap
