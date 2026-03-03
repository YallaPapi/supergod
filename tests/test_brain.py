"""Unit tests for dependency graph validation in orchestrator brain."""

from supergod.orchestrator.brain import Subtask, validate_subtask_graph


def test_validate_subtask_graph_accepts_valid_dag():
    subtasks = [
        Subtask(id="1", description="Step 1", depends_on=[]),
        Subtask(id="2", description="Step 2", depends_on=["1"]),
        Subtask(id="3", description="Step 3", depends_on=["2"]),
    ]
    ok, reason = validate_subtask_graph(subtasks)
    assert ok
    assert reason == ""


def test_validate_subtask_graph_rejects_unknown_dependency():
    subtasks = [
        Subtask(id="1", description="Step 1", depends_on=["missing"]),
    ]
    ok, reason = validate_subtask_graph(subtasks)
    assert not ok
    assert "unknown IDs" in reason


def test_validate_subtask_graph_rejects_cycle():
    subtasks = [
        Subtask(id="1", description="Step 1", depends_on=["2"]),
        Subtask(id="2", description="Step 2", depends_on=["1"]),
    ]
    ok, reason = validate_subtask_graph(subtasks)
    assert not ok
    assert "Circular dependency detected" in reason

