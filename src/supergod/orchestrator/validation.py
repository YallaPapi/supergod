"""Validation gates for completed subtasks before final merge."""

import asyncio


def _extract_head_sha(ls_remote_output: str) -> str:
    """Extract the first commit SHA from git ls-remote output."""
    if not ls_remote_output:
        return ""
    first_line = ls_remote_output.splitlines()[0].strip()
    if not first_line:
        return ""
    parts = first_line.split()
    return parts[0] if parts else ""


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
    branch_head = _extract_head_sha(out)
    if rc != 0 or not branch_head:
        return False, f"Validation failed: remote branch not found ({branch})"

    # Ensure reported commit matches remote branch head.
    if commit_sha != branch_head:
        return False, (
            "Validation failed: commit SHA does not match remote branch head "
            f"({commit_sha} != {branch_head})"
        )

    # Ensure branch head differs from main head (non-empty contribution).
    rc_main, out_main, _ = await _run(
        ["git", "ls-remote", "--heads", "origin", "main"], workdir
    )
    if rc_main != 0 or not out_main:
        return True, ""

    main_head = _extract_head_sha(out_main)
    if not main_head:
        return True, ""
    if branch_head == main_head:
        return False, "Validation failed: branch head equals main (no effective changes)"

    return True, ""
