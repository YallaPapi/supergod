from supergod.orchestrator import validation


async def test_validate_rejects_empty_commit_sha():
    ok, reason = await validation.validate_completed_subtask(
        workdir=".",
        branch="task/t1-s1",
        commit_sha="",
        has_remote=False,
    )
    assert not ok
    assert "empty commit SHA" in reason


async def test_validate_skips_git_calls_without_remote(monkeypatch):
    async def fail_run(cmd, cwd):  # pragma: no cover - should never run
        raise AssertionError("_run should not be called when has_remote=False")

    monkeypatch.setattr(validation, "_run", fail_run)
    ok, reason = await validation.validate_completed_subtask(
        workdir=".",
        branch="task/t1-s1",
        commit_sha="abc123",
        has_remote=False,
    )
    assert ok
    assert reason == ""


async def test_validate_remote_branch_lookup_failure_includes_stderr(monkeypatch):
    async def fake_run(cmd, cwd):
        return 2, "", "fatal: remote error"

    monkeypatch.setattr(validation, "_run", fake_run)
    ok, reason = await validation.validate_completed_subtask(
        workdir=".",
        branch="task/t1-s1",
        commit_sha="abc123",
        has_remote=True,
    )
    assert not ok
    assert "lookup failed" in reason
    assert "fatal: remote error" in reason


async def test_validate_remote_branch_not_found(monkeypatch):
    async def fake_run(cmd, cwd):
        return 0, "", ""

    monkeypatch.setattr(validation, "_run", fake_run)
    ok, reason = await validation.validate_completed_subtask(
        workdir=".",
        branch="task/t1-s1",
        commit_sha="abc123",
        has_remote=True,
    )
    assert not ok
    assert "remote branch not found" in reason


async def test_validate_accepts_when_main_lookup_unavailable(monkeypatch):
    calls = []

    async def fake_run(cmd, cwd):
        calls.append(cmd)
        if cmd[-1] == "task/t1-s1":
            return 0, "111111 refs/heads/task/t1-s1", ""
        return 1, "", "fatal: no main"

    monkeypatch.setattr(validation, "_run", fake_run)
    ok, reason = await validation.validate_completed_subtask(
        workdir=".",
        branch="task/t1-s1",
        commit_sha="abc123",
        has_remote=True,
    )
    assert ok
    assert reason == ""
    assert len(calls) == 2


async def test_validate_rejects_malformed_ls_remote_output(monkeypatch):
    async def fake_run(cmd, cwd):
        if cmd[-1] == "task/t1-s1":
            return 0, "   ", ""
        return 0, "222222 refs/heads/main", ""

    monkeypatch.setattr(validation, "_run", fake_run)
    ok, reason = await validation.validate_completed_subtask(
        workdir=".",
        branch="task/t1-s1",
        commit_sha="abc123",
        has_remote=True,
    )
    assert not ok
    assert "remote branch not found" in reason


async def test_validate_rejects_when_branch_equals_main(monkeypatch):
    async def fake_run(cmd, cwd):
        if cmd[-1] == "task/t1-s1":
            return 0, "abc refs/heads/task/t1-s1", ""
        return 0, "abc refs/heads/main", ""

    monkeypatch.setattr(validation, "_run", fake_run)
    ok, reason = await validation.validate_completed_subtask(
        workdir=".",
        branch="task/t1-s1",
        commit_sha="abc123",
        has_remote=True,
    )
    assert not ok
    assert "equals main" in reason


async def test_validate_accepts_when_branch_differs_from_main(monkeypatch):
    async def fake_run(cmd, cwd):
        if cmd[-1] == "task/t1-s1":
            return 0, "abc refs/heads/task/t1-s1", ""
        return 0, "def refs/heads/main", ""

    monkeypatch.setattr(validation, "_run", fake_run)
    ok, reason = await validation.validate_completed_subtask(
        workdir=".",
        branch="task/t1-s1",
        commit_sha="abc123",
        has_remote=True,
    )
    assert ok
    assert reason == ""

