from unittest.mock import AsyncMock, patch

from supergod.orchestrator.validation import (
    _extract_head_sha,
    validate_completed_subtask,
)


async def test_validate_fails_on_empty_commit_sha():
    ok, reason = await validate_completed_subtask(
        workdir="/tmp",
        branch="task/t1-s1",
        commit_sha="",
        has_remote=True,
    )
    assert ok is False
    assert "empty commit SHA" in reason


async def test_validate_skips_remote_checks_when_no_remote():
    ok, reason = await validate_completed_subtask(
        workdir="/tmp",
        branch="task/t1-s1",
        commit_sha="abc123",
        has_remote=False,
    )
    assert ok is True
    assert reason == ""


async def test_validate_fails_when_branch_not_found():
    with patch(
        "supergod.orchestrator.validation._run",
        AsyncMock(return_value=(0, "", "")),
    ):
        ok, reason = await validate_completed_subtask(
            workdir="/tmp",
            branch="task/t1-s1",
            commit_sha="abc123",
            has_remote=True,
        )
    assert ok is False
    assert "remote branch not found" in reason


async def test_validate_fails_when_commit_sha_mismatch():
    with patch(
        "supergod.orchestrator.validation._run",
        AsyncMock(
            side_effect=[
                (0, "deadbeef\trefs/heads/task/t1-s1", ""),
            ]
        ),
    ):
        ok, reason = await validate_completed_subtask(
            workdir="/tmp",
            branch="task/t1-s1",
            commit_sha="abc123",
            has_remote=True,
        )
    assert ok is False
    assert "does not match remote branch head" in reason


async def test_validate_succeeds_when_main_lookup_fails():
    with patch(
        "supergod.orchestrator.validation._run",
        AsyncMock(
            side_effect=[
                (0, "abc123\trefs/heads/task/t1-s1", ""),
                (1, "", "fatal: missing"),
            ]
        ),
    ):
        ok, reason = await validate_completed_subtask(
            workdir="/tmp",
            branch="task/t1-s1",
            commit_sha="abc123",
            has_remote=True,
        )
    assert ok is True
    assert reason == ""


async def test_validate_fails_when_branch_equals_main():
    with patch(
        "supergod.orchestrator.validation._run",
        AsyncMock(
            side_effect=[
                (0, "abc123\trefs/heads/task/t1-s1", ""),
                (0, "abc123\trefs/heads/main", ""),
            ]
        ),
    ):
        ok, reason = await validate_completed_subtask(
            workdir="/tmp",
            branch="task/t1-s1",
            commit_sha="abc123",
            has_remote=True,
        )
    assert ok is False
    assert "branch head equals main" in reason


async def test_validate_succeeds_when_branch_differs_from_main():
    with patch(
        "supergod.orchestrator.validation._run",
        AsyncMock(
            side_effect=[
                (0, "abc123\trefs/heads/task/t1-s1", ""),
                (0, "ffff99\trefs/heads/main", ""),
            ]
        ),
    ):
        ok, reason = await validate_completed_subtask(
            workdir="/tmp",
            branch="task/t1-s1",
            commit_sha="abc123",
            has_remote=True,
        )
    assert ok is True
    assert reason == ""


def test_extract_head_sha_handles_empty_and_multiline():
    assert _extract_head_sha("") == ""
    assert _extract_head_sha("\n") == ""
    out = "abc123\trefs/heads/task/t1-s1\ndef456\trefs/heads/main"
    assert _extract_head_sha(out) == "abc123"
