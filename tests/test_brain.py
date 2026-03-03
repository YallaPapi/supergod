"""Tests for orchestrator brain helpers."""

from unittest.mock import patch

from supergod.orchestrator import brain
from supergod.worker.codex_runner import CodexResult


async def test_replan_check_parses_valid_json():
    payload = '{"action":"continue","reason":"plan still valid"}'
    with patch(
        "supergod.orchestrator.brain.run_codex_collect",
        return_value=CodexResult(final_message=payload, return_code=0),
    ):
        plan = await brain.replan_check(
            original_prompt="Build API",
            completed_subtasks=[{"subtask_id": "t1-1", "prompt": "done"}],
            remaining_subtasks=[{"subtask_id": "t1-2", "prompt": "next"}],
        )
    assert plan["action"] == "continue"
    assert "reason" in plan


async def test_replan_check_invalid_json_falls_back_to_continue():
    with patch(
        "supergod.orchestrator.brain.run_codex_collect",
        return_value=CodexResult(final_message="not json", return_code=0),
    ):
        plan = await brain.replan_check(
            original_prompt="Build API",
            completed_subtasks=[],
            remaining_subtasks=[{"subtask_id": "t1-2", "prompt": "next"}],
        )
    assert plan["action"] == "continue"
    assert "parse" in plan["reason"].lower()


async def test_replan_check_invalid_action_falls_back_to_continue():
    payload = '{"action":"delete_everything","reason":"oops"}'
    with patch(
        "supergod.orchestrator.brain.run_codex_collect",
        return_value=CodexResult(final_message=payload, return_code=0),
    ):
        plan = await brain.replan_check(
            original_prompt="Build API",
            completed_subtasks=[],
            remaining_subtasks=[{"subtask_id": "t1-2", "prompt": "next"}],
        )
    assert plan["action"] == "continue"
    assert "invalid replanner action" in plan["reason"].lower()
