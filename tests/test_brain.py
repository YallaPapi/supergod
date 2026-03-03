"""Tests for orchestrator brain parsing and retry/fallback behavior."""

from unittest.mock import AsyncMock, patch

from supergod.orchestrator import brain
from supergod.worker.codex_runner import CodexResult


def test_extract_json_array_balanced_span_ignores_later_brackets():
    text = 'noise [{"id":"1","description":"a","depends_on":[]}] trailing [not json'
    items = brain._extract_json_array(text)
    assert isinstance(items, list)
    assert items[0]["id"] == "1"


def test_extract_json_object_balanced_span_ignores_later_braces():
    text = 'prefix {"status":"success","summary":"ok"} trailing {bad'
    obj = brain._extract_json_object(text)
    assert obj["status"] == "success"
    assert obj["summary"] == "ok"


async def test_decompose_task_retries_then_succeeds():
    bad = CodexResult(return_code=0, final_message="not-json")
    good = CodexResult(
        return_code=0,
        final_message='[{"id":"s1","description":"Do thing","depends_on":[]}]',
    )
    mock_collect = AsyncMock(side_effect=[bad, good])

    with patch("supergod.orchestrator.brain.run_codex_collect", mock_collect):
        subtasks = await brain.decompose_task("Build feature")

    assert len(subtasks) == 1
    assert subtasks[0].id == "s1"
    assert subtasks[0].description == "Do thing"
    assert mock_collect.await_count == 2


async def test_decompose_task_falls_back_on_nonzero_return_code():
    failed = CodexResult(return_code=1, final_message="boom")
    with patch(
        "supergod.orchestrator.brain.run_codex_collect",
        AsyncMock(return_value=failed),
    ):
        subtasks = await brain.decompose_task("Single step task")

    assert len(subtasks) == 1
    assert subtasks[0].description == "Single step task"
    assert subtasks[0].depends_on == []


async def test_evaluate_results_retries_then_returns_json():
    bad = CodexResult(return_code=0, final_message="```json\nnope\n```")
    good = CodexResult(
        return_code=0,
        final_message='{"status":"failure","summary":"tests broke","fix_tasks":[{"description":"fix test"}]}',
    )
    mock_collect = AsyncMock(side_effect=[bad, good])

    with patch("supergod.orchestrator.brain.run_codex_collect", mock_collect):
        result = await brain.evaluate_results("task", "failing tests")

    assert result["status"] == "failure"
    assert result["summary"] == "tests broke"
    assert result["fix_tasks"][0]["description"] == "fix test"
    assert mock_collect.await_count == 2
