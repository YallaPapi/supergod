"""Git management for the orchestrator — merging branches, running tests."""

import asyncio
import logging

log = logging.getLogger(__name__)


async def _run(cmd: list[str], cwd: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode().strip(), stderr.decode().strip()


async def _git(cwd: str, *args: str) -> tuple[int, str, str]:
    return await _run(["git", *args], cwd)


async def merge_branch(workdir: str, branch: str) -> tuple[bool, str]:
    """Merge a branch into the current branch (main). Returns (success, output)."""
    code, out, err = await _git(workdir, "merge", branch, "--no-edit")
    if code != 0:
        # Abort the failed merge
        await _git(workdir, "merge", "--abort")
        return False, err
    return True, out


async def merge_all_branches(
    workdir: str, branches: list[str]
) -> tuple[bool, list[str]]:
    """Merge multiple branches into main sequentially. Returns (all_ok, errors)."""
    report = await merge_all_branches_with_report(workdir, branches)
    return len(report["failed"]) == 0, report["errors"]


async def merge_all_branches_with_report(
    workdir: str, branches: list[str]
) -> dict:
    """Merge branches into main and return detailed success/failure report.

    Returns:
        {
            "merged": [branch, ...],
            "failed": {branch: error, ...},
            "errors": ["Merge conflict on ...", ...],
        }
    """
    # Make sure we're on main
    await _git(workdir, "checkout", "main")
    await _git(workdir, "fetch", "origin")
    await _git(workdir, "reset", "--hard", "origin/main")

    errors: list[str] = []
    merged: list[str] = []
    failed: dict[str, str] = {}
    for branch in branches:
        ok, output = await merge_branch(workdir, f"origin/{branch}")
        if not ok:
            errors.append(f"Merge conflict on {branch}: {output}")
            failed[branch] = output
            log.error("Failed to merge %s: %s", branch, output)
        else:
            merged.append(branch)

    return {"merged": merged, "failed": failed, "errors": errors}


async def run_tests(workdir: str, test_cmd: str = "pytest") -> tuple[bool, str]:
    """Run the test suite. Returns (passed, output).

    Exit code 0 = all tests passed, exit code 5 = no tests collected (also OK).
    """
    cmd = test_cmd.split()
    code, stdout, stderr = await _run(cmd, workdir)
    output = stdout + "\n" + stderr if stderr else stdout
    # pytest exit code 5 = no tests collected -- treat as pass
    return code in (0, 5), output


async def push_main(workdir: str) -> tuple[bool, str]:
    code, out, err = await _git(workdir, "push", "origin", "main")
    if code != 0:
        return False, err
    return True, out


async def delete_branch(workdir: str, branch: str) -> None:
    """Delete a remote branch after successful merge."""
    await _git(workdir, "push", "origin", "--delete", branch)
    await _git(workdir, "branch", "-d", branch)
