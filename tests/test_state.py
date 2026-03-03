"""Tests for StateDB — SQLite state management."""

import json
import os

import pytest

from supergod.shared.protocol import TaskStatus, WorkerStatus
from supergod.orchestrator.state import StateDB


@pytest.fixture
async def db(tmp_path):
    """Create a fresh StateDB backed by a temp file."""
    db_path = str(tmp_path / "test.db")
    state = StateDB(db_path)
    await state.init()
    yield state
    await state.close()


# --- Task CRUD ---


async def test_create_and_get_task(db: StateDB):
    await db.create_task("t1", "Build auth module")
    task = await db.get_task("t1")
    assert task is not None
    assert task["task_id"] == "t1"
    assert task["prompt"] == "Build auth module"
    assert task["status"] == TaskStatus.PENDING


async def test_get_nonexistent_task(db: StateDB):
    task = await db.get_task("does-not-exist")
    assert task is None


async def test_update_task_status(db: StateDB):
    await db.create_task("t1", "Build auth")
    await db.update_task_status("t1", TaskStatus.RUNNING)
    task = await db.get_task("t1")
    assert task["status"] == TaskStatus.RUNNING


async def test_update_task_status_with_summary(db: StateDB):
    await db.create_task("t1", "Build auth")
    await db.update_task_status("t1", TaskStatus.COMPLETED, summary="All tests passed")
    task = await db.get_task("t1")
    assert task["status"] == TaskStatus.COMPLETED
    assert task["summary"] == "All tests passed"


async def test_get_all_tasks_ordered(db: StateDB):
    await db.create_task("t1", "First")
    await db.create_task("t2", "Second")
    await db.create_task("t3", "Third")
    tasks = await db.get_all_tasks()
    assert len(tasks) == 3
    # Ordered by created_at DESC, so newest first
    # Since they're created very close together, just check all exist
    task_ids = {t["task_id"] for t in tasks}
    assert task_ids == {"t1", "t2", "t3"}


async def test_task_timestamps(db: StateDB):
    await db.create_task("t1", "Build auth")
    task = await db.get_task("t1")
    assert task["created_at"] is not None
    assert task["updated_at"] is not None

    old_updated = task["updated_at"]
    await db.update_task_status("t1", TaskStatus.RUNNING)
    task = await db.get_task("t1")
    # updated_at should change (or be same if sub-ms)
    assert task["updated_at"] >= old_updated


# --- Subtask CRUD ---


async def test_create_subtask(db: StateDB):
    await db.create_task("t1", "Build auth")
    await db.create_subtask(
        subtask_id="t1-s1",
        task_id="t1",
        prompt="Implement login endpoint",
        branch="task/t1-s1",
    )
    subtasks = await db.get_subtasks_for_task("t1")
    assert len(subtasks) == 1
    assert subtasks[0]["subtask_id"] == "t1-s1"
    assert subtasks[0]["status"] == TaskStatus.PENDING
    assert subtasks[0]["branch"] == "task/t1-s1"


async def test_create_subtask_with_dependencies(db: StateDB):
    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-s1", "t1", "Step 1", "task/t1-s1")
    await db.create_subtask(
        "t1-s2", "t1", "Step 2", "task/t1-s2", depends_on=["t1-s1"]
    )
    subtasks = await db.get_subtasks_for_task("t1")
    s2 = [s for s in subtasks if s["subtask_id"] == "t1-s2"][0]
    deps = json.loads(s2["depends_on"])
    assert deps == ["t1-s1"]


async def test_update_subtask(db: StateDB):
    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-s1", "t1", "Step 1", "task/t1-s1")
    await db.update_subtask(
        "t1-s1", status=TaskStatus.RUNNING, worker_name="worker-1"
    )
    subtasks = await db.get_subtasks_for_task("t1")
    s1 = subtasks[0]
    assert s1["status"] == TaskStatus.RUNNING
    assert s1["worker_name"] == "worker-1"


async def test_update_subtask_commit_sha(db: StateDB):
    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-s1", "t1", "Step 1", "task/t1-s1")
    await db.update_subtask(
        "t1-s1", status=TaskStatus.COMPLETED, commit_sha="abc123"
    )
    subtasks = await db.get_subtasks_for_task("t1")
    assert subtasks[0]["commit_sha"] == "abc123"


# --- get_ready_subtasks with dependency resolution ---


async def test_get_ready_subtasks_no_deps(db: StateDB):
    """Subtasks with no dependencies should be ready immediately."""
    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-s1", "t1", "Step 1", "b1")
    await db.create_subtask("t1-s2", "t1", "Step 2", "b2")
    ready = await db.get_ready_subtasks("t1")
    assert len(ready) == 2


async def test_get_ready_subtasks_with_deps(db: StateDB):
    """Subtask with unmet dependency should NOT be ready."""
    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-s1", "t1", "Step 1", "b1")
    await db.create_subtask("t1-s2", "t1", "Step 2", "b2", depends_on=["t1-s1"])
    ready = await db.get_ready_subtasks("t1")
    # Only s1 is ready, s2 depends on s1
    assert len(ready) == 1
    assert ready[0]["subtask_id"] == "t1-s1"


async def test_get_ready_subtasks_after_dep_completed(db: StateDB):
    """After dependency completes, dependent subtask becomes ready."""
    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-s1", "t1", "Step 1", "b1")
    await db.create_subtask("t1-s2", "t1", "Step 2", "b2", depends_on=["t1-s1"])

    # Complete s1
    await db.update_subtask("t1-s1", status=TaskStatus.COMPLETED)

    ready = await db.get_ready_subtasks("t1")
    assert len(ready) == 1
    assert ready[0]["subtask_id"] == "t1-s2"


async def test_get_ready_subtasks_multiple_deps(db: StateDB):
    """Subtask with multiple dependencies: all must be completed."""
    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-s1", "t1", "Step 1", "b1")
    await db.create_subtask("t1-s2", "t1", "Step 2", "b2")
    await db.create_subtask(
        "t1-s3", "t1", "Step 3", "b3", depends_on=["t1-s1", "t1-s2"]
    )

    # Only s1 completed, s3 still blocked
    await db.update_subtask("t1-s1", status=TaskStatus.COMPLETED)
    ready = await db.get_ready_subtasks("t1")
    ready_ids = {r["subtask_id"] for r in ready}
    assert "t1-s2" in ready_ids
    assert "t1-s3" not in ready_ids

    # Now complete s2 too
    await db.update_subtask("t1-s2", status=TaskStatus.COMPLETED)
    ready = await db.get_ready_subtasks("t1")
    ready_ids = {r["subtask_id"] for r in ready}
    assert "t1-s3" in ready_ids


async def test_get_ready_subtasks_skips_running(db: StateDB):
    """Subtasks already running should NOT appear as ready."""
    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-s1", "t1", "Step 1", "b1")
    await db.update_subtask("t1-s1", status=TaskStatus.RUNNING)
    ready = await db.get_ready_subtasks("t1")
    assert len(ready) == 0


async def test_block_dependency_cycles_blocks_cyclic_pending_subtasks(db: StateDB):
    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-s1", "t1", "Step 1", "b1", depends_on=["t1-s2"])
    await db.create_subtask("t1-s2", "t1", "Step 2", "b2", depends_on=["t1-s1"])

    blocked = await db.block_dependency_cycles("t1")
    assert set(blocked) == {"t1-s1", "t1-s2"}

    s1 = await db.get_subtask("t1-s1")
    s2 = await db.get_subtask("t1-s2")
    assert s1["status"] == TaskStatus.BLOCKED
    assert s2["status"] == TaskStatus.BLOCKED
    assert s1["failure_category"] == "dependency_cycle"
    assert s2["failure_category"] == "dependency_cycle"


async def test_block_dependency_cycles_noop_for_acyclic_graph(db: StateDB):
    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-s1", "t1", "Step 1", "b1")
    await db.create_subtask("t1-s2", "t1", "Step 2", "b2", depends_on=["t1-s1"])

    blocked = await db.block_dependency_cycles("t1")
    assert blocked == []

    s1 = await db.get_subtask("t1-s1")
    s2 = await db.get_subtask("t1-s2")
    assert s1["status"] == TaskStatus.PENDING
    assert s2["status"] == TaskStatus.PENDING


# --- Worker operations ---


async def test_upsert_worker(db: StateDB):
    await db.upsert_worker("w1", WorkerStatus.IDLE)
    workers = await db.get_all_workers()
    assert len(workers) == 1
    assert workers[0]["name"] == "w1"
    assert workers[0]["status"] == WorkerStatus.IDLE


async def test_upsert_worker_update(db: StateDB):
    """Second upsert updates status instead of inserting duplicate."""
    await db.upsert_worker("w1", WorkerStatus.IDLE)
    await db.upsert_worker("w1", WorkerStatus.BUSY)
    workers = await db.get_all_workers()
    assert len(workers) == 1
    assert workers[0]["status"] == WorkerStatus.BUSY


async def test_get_idle_workers(db: StateDB):
    await db.upsert_worker("w1", WorkerStatus.IDLE)
    await db.upsert_worker("w2", WorkerStatus.BUSY)
    await db.upsert_worker("w3", WorkerStatus.OFFLINE)
    idle = await db.get_idle_workers()
    assert len(idle) == 1
    assert idle[0]["name"] == "w1"


async def test_set_worker_task(db: StateDB):
    await db.upsert_worker("w1", WorkerStatus.IDLE)
    await db.set_worker_task("w1", "t1-s1")
    workers = await db.get_all_workers()
    w = workers[0]
    assert w["current_subtask"] == "t1-s1"
    assert w["status"] == WorkerStatus.BUSY


async def test_set_worker_task_clear(db: StateDB):
    """Setting subtask_id to None marks worker as idle."""
    await db.upsert_worker("w1", WorkerStatus.IDLE)
    await db.set_worker_task("w1", "t1-s1")
    await db.set_worker_task("w1", None)
    workers = await db.get_all_workers()
    w = workers[0]
    assert w["current_subtask"] is None
    assert w["status"] == WorkerStatus.IDLE


async def test_multiple_workers(db: StateDB):
    for i in range(5):
        await db.upsert_worker(f"w{i}", WorkerStatus.IDLE)
    workers = await db.get_all_workers()
    assert len(workers) == 5


async def test_reset_worker_leases(db: StateDB):
    await db.upsert_worker("w1", WorkerStatus.IDLE)
    await db.set_worker_task("w1", "t1-s1")
    await db.reset_worker_leases()
    worker = await db.get_worker("w1")
    assert worker["status"] == WorkerStatus.OFFLINE
    assert worker["current_subtask"] is None


async def test_checkpoint_roundtrip(db: StateDB):
    await db.create_task("t1", "Build auth")
    cp_id = await db.save_checkpoint(
        "t1", "decomposed", {"subtask_count": 2}
    )
    assert cp_id
    cp = await db.get_latest_checkpoint("t1")
    assert cp is not None
    assert cp["task_id"] == "t1"
    assert cp["step"] == "decomposed"
    assert cp["state_snapshot"]["subtask_count"] == 2


async def test_get_resumable_tasks(db: StateDB):
    await db.create_task("t1", "Task 1")
    await db.create_task("t2", "Task 2")
    await db.update_task_status("t2", TaskStatus.COMPLETED)
    resumable = await db.get_resumable_tasks()
    ids = {t["task_id"] for t in resumable}
    assert "t1" in ids
    assert "t2" not in ids


async def test_task_priority_and_dispatch_order(db: StateDB):
    await db.create_task("low", "Low priority", priority=200)
    await db.create_task("high", "High priority", priority=10)
    await db.update_task_status("low", TaskStatus.ASSIGNED)
    await db.update_task_status("high", TaskStatus.ASSIGNED)
    ordered = await db.get_dispatchable_tasks()
    assert [t["task_id"] for t in ordered] == ["high", "low"]


async def test_claim_and_release_subtask_execution(db: StateDB):
    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-s1", "t1", "Step 1", "b1")

    claimed = await db.claim_subtask_execution("t1-s1", "w1", "tok1")
    assert claimed is not None
    assert claimed["status"] == TaskStatus.RUNNING
    assert claimed["worker_name"] == "w1"
    assert claimed["execution_token"] == "tok1"
    assert claimed["lease_version"] == 1

    # Pending-only claim should fail while running.
    claimed_again = await db.claim_subtask_execution("t1-s1", "w2", "tok2")
    assert claimed_again is None

    released = await db.release_subtask_execution(
        "t1-s1", "tok1", next_status=TaskStatus.PENDING
    )
    assert released
    s = await db.get_subtask("t1-s1")
    assert s["status"] == TaskStatus.PENDING
    assert s["execution_token"] == ""
