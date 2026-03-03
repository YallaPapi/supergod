"""Tests for orchestrator replan flow."""

import pytest

from supergod.orchestrator.scheduler import Scheduler
from supergod.orchestrator.state import StateDB
from supergod.shared.protocol import TaskStatus


@pytest.fixture
async def server_with_db(tmp_path):
    import supergod.orchestrator.server as srv

    old_db = srv.db
    old_scheduler = srv.scheduler
    old_workdir = srv.workdir
    old_counts = dict(srv._task_completion_counts)

    test_db = StateDB(str(tmp_path / "test.db"))
    await test_db.init()
    test_scheduler = Scheduler(test_db)

    srv.db = test_db
    srv.scheduler = test_scheduler
    srv.workdir = str(tmp_path)
    srv._task_completion_counts = {}

    yield srv, test_db, test_scheduler

    await test_db.close()
    srv.db = old_db
    srv.scheduler = old_scheduler
    srv.workdir = old_workdir
    srv._task_completion_counts = old_counts


def test_has_dependency_cycle_detects_cycle():
    import supergod.orchestrator.server as srv

    graph = {
        "t1-a": {"t1-b"},
        "t1-b": {"t1-a"},
    }
    assert srv._has_dependency_cycle(graph) is True


def test_has_dependency_cycle_handles_acyclic_graph():
    import supergod.orchestrator.server as srv

    graph = {
        "t1-a": set(),
        "t1-b": {"t1-a"},
        "t1-c": {"t1-b"},
    }
    assert srv._has_dependency_cycle(graph) is False


async def test_create_replan_subtasks_rejects_cycle(server_with_db):
    srv, db, _ = server_with_db
    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-1", "t1", "Initial step", "task/t1-1")

    created, err = await srv._create_replan_subtasks(
        "t1",
        "Build auth",
        [
            {"id": "2", "description": "Step 2", "depends_on": ["3"]},
            {"id": "3", "description": "Step 3", "depends_on": ["2"]},
        ],
    )
    assert created == 0
    assert "cycle" in err.lower()


async def test_apply_replan_plan_adds_subtasks(server_with_db):
    srv, db, _ = server_with_db
    await db.create_task("t1", "Build auth")
    await db.create_subtask("t1-1", "t1", "Initial step", "task/t1-1")
    await db.update_subtask("t1-1", status=TaskStatus.COMPLETED)

    await srv._apply_replan_plan(
        "t1",
        "Build auth",
        {
            "action": "add_subtasks",
            "reason": "Need hardening pass",
            "subtasks": [
                {"id": "hardening", "description": "Add auth hardening checks"}
            ],
        },
    )

    subtasks = await db.get_subtasks_for_task("t1")
    ids = {s["subtask_id"] for s in subtasks}
    assert "t1-hardening" in ids


async def test_maybe_replan_respects_interval(server_with_db, monkeypatch):
    srv, db, _ = server_with_db
    await db.create_task("t1", "Build auth")
    await db.update_task_status("t1", TaskStatus.ASSIGNED)
    await db.create_subtask("t1-1", "t1", "Done", "task/t1-1")
    await db.create_subtask("t1-2", "t1", "Next", "task/t1-2")
    await db.update_subtask("t1-1", status=TaskStatus.COMPLETED)

    calls = {"count": 0}

    async def fake_replan_check(**kwargs):
        calls["count"] += 1
        return {"action": "continue", "reason": "ok"}

    monkeypatch.setattr(srv, "PLANNING_INTERVAL", 2)
    monkeypatch.setattr(srv, "replan_check", fake_replan_check)

    await srv._maybe_replan_task("t1")
    await srv._maybe_replan_task("t1")

    assert calls["count"] == 1
