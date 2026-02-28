"""Git operations for worker servers."""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)


async def _run_git(workdir: str, *args: str) -> str:
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
        raise RuntimeError(f"git {args[0]} failed: {err}")
    return stdout.decode().strip()


async def pull(workdir: str) -> str:
    return await _run_git(workdir, "pull", "--rebase", "origin", "main")


async def checkout_branch(workdir: str, branch: str) -> str:
    # Create branch from main, or switch if it exists
    try:
        await _run_git(workdir, "checkout", "-b", branch, "origin/main")
    except RuntimeError:
        await _run_git(workdir, "checkout", branch)
    return branch


async def add_all(workdir: str) -> str:
    return await _run_git(workdir, "add", "-A")


async def commit(workdir: str, message: str) -> str:
    try:
        return await _run_git(workdir, "commit", "-m", message)
    except RuntimeError as e:
        if "nothing to commit" in str(e):
            log.info("Nothing to commit")
            return ""
        raise


async def push(workdir: str, branch: str) -> str:
    return await _run_git(workdir, "push", "-u", "origin", branch)


async def get_head_sha(workdir: str) -> str:
    return await _run_git(workdir, "rev-parse", "HEAD")


async def fetch(workdir: str) -> str:
    return await _run_git(workdir, "fetch", "origin")


async def checkout_main(workdir: str) -> str:
    return await _run_git(workdir, "checkout", "main")
