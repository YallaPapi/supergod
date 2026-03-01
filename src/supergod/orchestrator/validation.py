"""Validation gates for completed subtasks before final merge."""

import asyncio


async def _run(cmd: list[str], cwd: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return (
        proc.returncode,
        stdout.decode(errors="replace").strip(),
        stderr.decode(errors="replace").strip(),
    )


async def validate_completed_subtask(
    workdir: str,
    branch: str,
    commit_sha: str,
    has_remote: bool,
) -> tuple[bool, str]:
    """Validate minimum correctness of worker output."""
    if not commit_sha:
        return False, "Validation failed: empty commit SHA"

    if not has_remote:
        return True, ""

    # Ensure branch exists on remote.
    rc, out, _ = await _run(
        ["git", "ls-remote", "--heads", "origin", branch], workdir
    )
    if rc != 0 or not out:
        return False, f"Validation failed: remote branch not found ({branch})"

    # Ensure branch head differs from main head (non-empty contribution).
    rc_main, out_main, _ = await _run(
        ["git", "ls-remote", "--heads", "origin", "main"], workdir
    )
    if rc_main != 0 or not out_main:
        return True, ""

    main_head = out_main.split()[0]
    branch_head = out.split()[0]
    if branch_head == main_head:
        return False, "Validation failed: branch head equals main (no effective changes)"

    return True, ""
