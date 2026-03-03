"""Tests for brain decomposition parsing and dependency validation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from supergod.orchestrator.brain import (
    Subtask,
    decompose_task,
    validate_subtask_dependencies,
)


def test_validate_subtask_dependencies_rejects_unknown_dep():
    subtasks = [
        Subtask(id="1", description="First", depends_on=[]),
        Subtask(id="2", description="Second", depends_on=["missing"]),
    ]
    valid, error = validate_subtask_dependencies(subtasks)
    assert not valid
    assert "unknown" in error


def test_validate_subtask_dependencies_rejects_cycle():
    subtasks = [
        Subtask(id="1", description="First", depends_on=["2"]),
        Subtask(id="2", description="Second", depends_on=["1"]),
    ]
    valid, error = validate_subtask_dependencies(subtasks)
    assert not valid
    assert "circular dependency" in error


async def test_decompose_task_falls_back_on_invalid_dependency_graph():
    result = SimpleNamespace(
        return_code=0,
        final_message=(
            '[{"id":"1","description":"one","depends_on":["2"]},'
            '{"id":"2","description":"two","depends_on":["1"]}]'
        ),
        events=[],
    )

    with patch(
        "supergod.orchestrator.brain.run_codex_collect",
        AsyncMock(return_value=result),
    ):
        subtasks = await decompose_task("Build thing", workdir=".")

    assert len(subtasks) == 1
    assert subtasks[0].description == "Build thing"
    assert subtasks[0].depends_on == []
