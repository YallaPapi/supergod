"""Tests for orchestrator brain decomposition and dependency validation."""

from unittest.mock import AsyncMock, patch

from supergod.orchestrator.brain import (
    Subtask,
    _validate_subtask_dependency_graph,
    decompose_task,
)
from supergod.worker.codex_runner import CodexResult


def test_validate_subtask_dependency_graph_valid():
    subtasks = [
        Subtask(id="1", description="Step 1", depends_on=[]),
        Subtask(id="2", description="Step 2", depends_on=["1"]),
        Subtask(id="3", description="Step 3", depends_on=["2"]),
    ]
    assert _validate_subtask_dependency_graph(subtasks) is None


def test_validate_subtask_dependency_graph_detects_cycle():
    subtasks = [
        Subtask(id="1", description="Step 1", depends_on=["3"]),
        Subtask(id="2", description="Step 2", depends_on=["1"]),
        Subtask(id="3", description="Step 3", depends_on=["2"]),
    ]
    assert _validate_subtask_dependency_graph(subtasks) == "circular_dependency"


def test_validate_subtask_dependency_graph_detects_self_dependency():
    subtasks = [Subtask(id="1", description="Step 1", depends_on=["1"])]
    assert (
        _validate_subtask_dependency_graph(subtasks)
        == "self_dependency:1"
    )


def test_validate_subtask_dependency_graph_detects_unknown_dependency():
    subtasks = [
        Subtask(id="1", description="Step 1", depends_on=[]),
        Subtask(id="2", description="Step 2", depends_on=["404"]),
    ]
    assert (
        _validate_subtask_dependency_graph(subtasks)
        == "unknown_dependency:2->404"
    )


async def test_decompose_task_falls_back_when_dependency_graph_has_cycle():
    codex_output = (
        '[{"id":"1","description":"Step 1","depends_on":["3"]},'
        '{"id":"2","description":"Step 2","depends_on":["1"]},'
        '{"id":"3","description":"Step 3","depends_on":["2"]}]'
    )
    fake_result = CodexResult(events=[], final_message=codex_output, return_code=0)

    with patch(
        "supergod.orchestrator.brain.run_codex_collect",
        AsyncMock(return_value=fake_result),
    ):
        subtasks = await decompose_task("Build auth")

    assert len(subtasks) == 1
    assert subtasks[0].description == "Build auth"
    assert subtasks[0].depends_on == []

