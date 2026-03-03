"""Tests for orchestrator brain helpers."""

from supergod.orchestrator.brain import Subtask, validate_subtask_dependencies


def test_validate_subtask_dependencies_accepts_dag():
    subtasks = [
        Subtask(id="1", description="A", depends_on=[]),
        Subtask(id="2", description="B", depends_on=["1"]),
        Subtask(id="3", description="C", depends_on=["2"]),
    ]
    ok, reason = validate_subtask_dependencies(subtasks)
    assert ok
    assert reason == ""


def test_validate_subtask_dependencies_rejects_duplicate_ids():
    subtasks = [
        Subtask(id="1", description="A", depends_on=[]),
        Subtask(id="1", description="B", depends_on=[]),
    ]
    ok, reason = validate_subtask_dependencies(subtasks)
    assert not ok
    assert "duplicate subtask id(s): 1" == reason


def test_validate_subtask_dependencies_rejects_unknown_dependency():
    subtasks = [
        Subtask(id="1", description="A", depends_on=["ghost"]),
    ]
    ok, reason = validate_subtask_dependencies(subtasks)
    assert not ok
    assert "unknown dependency id(s): ghost" == reason


def test_validate_subtask_dependencies_rejects_cycle():
    subtasks = [
        Subtask(id="1", description="A", depends_on=["2"]),
        Subtask(id="2", description="B", depends_on=["3"]),
        Subtask(id="3", description="C", depends_on=["1"]),
    ]
    ok, reason = validate_subtask_dependencies(subtasks)
    assert not ok
    assert "circular dependency detected:" in reason
