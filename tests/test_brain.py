"""Tests for orchestrator brain dependency normalization."""

from supergod.orchestrator.brain import (
    Subtask,
    _normalize_subtask_dependencies,
)


def test_normalize_removes_unknown_and_self_dependencies():
    subtasks = [
        Subtask(id="1", description="first", depends_on=["1", "missing"]),
        Subtask(id="2", description="second", depends_on=["1", "1"]),
    ]

    normalized = _normalize_subtask_dependencies(subtasks)

    assert normalized[0].depends_on == []
    assert normalized[1].depends_on == ["1"]


def test_normalize_breaks_simple_cycle():
    subtasks = [
        Subtask(id="1", description="first", depends_on=["2"]),
        Subtask(id="2", description="second", depends_on=["1"]),
        Subtask(id="3", description="third", depends_on=["2"]),
    ]

    normalized = _normalize_subtask_dependencies(subtasks)
    by_id = {s.id: s for s in normalized}

    # Cycle members are reset; downstream nodes keep valid dependencies.
    assert by_id["1"].depends_on == []
    assert by_id["2"].depends_on == []
    assert by_id["3"].depends_on == ["2"]


def test_normalize_breaks_three_node_cycle():
    subtasks = [
        Subtask(id="a", description="A", depends_on=["c"]),
        Subtask(id="b", description="B", depends_on=["a"]),
        Subtask(id="c", description="C", depends_on=["b"]),
    ]

    normalized = _normalize_subtask_dependencies(subtasks)
    assert all(not subtask.depends_on for subtask in normalized)
