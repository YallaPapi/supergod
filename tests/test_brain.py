"""Tests for orchestrator brain decomposition and dependency validation."""

from unittest.mock import AsyncMock, patch

import pytest

from supergod.orchestrator.brain import (
    Subtask,
    _validate_dependency_graph,
    decompose_task,
)
from supergod.worker.codex_runner import CodexResult

pytestmark = pytest.mark.asyncio


def test_validate_dependency_graph_accepts_valid_dag():
    subtasks = [
        Subtask(id="1", description="first", depends_on=[]),
        Subtask(id="2", description="second", depends_on=["1"]),
        Subtask(id="3", description="third", depends_on=["2"]),
    ]
    assert _validate_dependency_graph(subtasks) is None


def test_validate_dependency_graph_rejects_cycle():
    subtasks = [
        Subtask(id="1", description="first", depends_on=["2"]),
        Subtask(id="2", description="second", depends_on=["1"]),
    ]
    assert _validate_dependency_graph(subtasks) == "circular_dependencies"


async def test_decompose_task_falls_back_on_unknown_dependency():
    codex_output = '[{"id":"1","description":"a","depends_on":["9"]}]'
    result = CodexResult(events=[], final_message=codex_output, return_code=0)

    with patch(
        "supergod.orchestrator.brain.run_codex_collect",
        AsyncMock(return_value=result),
    ):
        subtasks = await decompose_task("build feature", workdir=".")

    assert len(subtasks) == 1
    assert subtasks[0].description == "build feature"
    assert subtasks[0].depends_on == []


async def test_decompose_task_falls_back_on_duplicate_ids():
    codex_output = (
        '[{"id":"1","description":"a","depends_on":[]},'
        '{"id":"1","description":"b","depends_on":[]}]'
    )
    result = CodexResult(events=[], final_message=codex_output, return_code=0)

    with patch(
        "supergod.orchestrator.brain.run_codex_collect",
        AsyncMock(return_value=result),
    ):
        subtasks = await decompose_task("build feature", workdir=".")

    assert len(subtasks) == 1
    assert subtasks[0].description == "build feature"
    assert subtasks[0].depends_on == []
