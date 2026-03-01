"""Git operations for worker servers."""

import asyncio
import logging

log = logging.getLogger(__name__)


async def _run_git(workdir: str, *args: str, allow_fail: bool = False) -> str:
    """Run a git command and return stdout."""
    cmd = ["git", *args]
    log.info("git %s (cwd=%s)", " ".join(args), workdir)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=workdir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode().strip()
        if allow_fail:
            log.warning("git %s failed (non-fatal): %s", args[0], err)
            return ""
        raise RuntimeError(f"git {args[0]} failed: {err}")
    return stdout.decode().strip()


async def has_remote(workdir: str) -> bool:
    """Check if the repo has any remotes configured."""
    result = await _run_git(workdir, "remote", allow_fail=True)
    return bool(result.strip())


async def pull(workdir: str) -> str:
    return await _run_git(workdir, "pull", "--rebase", "origin", "main")


async def checkout_branch(workdir: str, branch: str) -> str:
    # Reuse remote branch when it exists (retry/idempotent behavior).
    base_ref = "origin/main"
    if await remote_branch_exists(workdir, branch):
        base_ref = f"origin/{branch}"
    try:
        await _run_git(workdir, "checkout", "-b", branch, base_ref)
    except RuntimeError:
        await _run_git(workdir, "checkout", branch)
    return branch


async def add_all(workdir: str) -> str:
    return await _run_git(workdir, "add", "-A")


async def commit(workdir: str, message: str) -> str:
    try:
        return await _run_git(workdir, "commit", "-m", message)
    except RuntimeError as e:
        status = await _run_git(
            workdir, "status", "--porcelain", allow_fail=True
        )
        if not status.strip():
            log.info("Nothing to commit")
            return ""
        raise RuntimeError(f"git commit failed with pending changes: {e}") from e


async def push(workdir: str, branch: str) -> str:
    return await _run_git(workdir, "push", "-u", "origin", branch)


async def get_head_sha(workdir: str) -> str:
    return await _run_git(workdir, "rev-parse", "HEAD")


async def fetch(workdir: str) -> str:
    return await _run_git(workdir, "fetch", "origin")


async def checkout_main(workdir: str) -> str:
    return await _run_git(workdir, "checkout", "main")


async def create_worktree(
    repo_dir: str,
    worktree_dir: str,
    branch: str,
) -> str:
    """Create/reset an isolated worktree for a subtask branch."""
    # Ensure stale path is cleaned up first.
    await remove_worktree(repo_dir, worktree_dir)
    base_ref = "origin/main"
    if await remote_branch_exists(repo_dir, branch):
        base_ref = f"origin/{branch}"
    try:
        return await _run_git(
            repo_dir, "worktree", "add", "-B", branch, worktree_dir, base_ref
        )
    except RuntimeError:
        # Fallback for local-only repos
        return await _run_git(
            repo_dir, "worktree", "add", "-B", branch, worktree_dir, "main"
        )


async def remove_worktree(repo_dir: str, worktree_dir: str) -> None:
    await _run_git(
        repo_dir, "worktree", "remove", "--force", worktree_dir, allow_fail=True
    )


async def remote_branch_exists(workdir: str, branch: str) -> bool:
    out = await _run_git(
        workdir,
        "ls-remote",
        "--heads",
        "origin",
        branch,
        allow_fail=True,
    )
    return bool(out.strip())
