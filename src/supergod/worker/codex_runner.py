"""Run Codex CLI in non-interactive mode and stream JSONL events.

This is the core building block used by both workers (to execute tasks)
and the orchestrator (to think — decompose tasks, evaluate results).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import AsyncGenerator

from supergod.shared.config import CODEX_BIN, CODEX_TIMEOUT

log = logging.getLogger(__name__)


class CodexError(Exception):
    pass


@dataclass
class CodexResult:
    events: list[dict] = field(default_factory=list)
    final_message: str = ""
    return_code: int = 0


async def run_codex(
    prompt: str,
    workdir: str = ".",
    timeout: int = CODEX_TIMEOUT,
    extra_args: list[str] | None = None,
) -> AsyncGenerator[dict, None]:
    """Spawn codex exec --json --full-auto and yield JSONL events.

    Args:
        prompt: The task prompt to send to Codex.
        workdir: Working directory for Codex to operate in.
        timeout: Max seconds before killing the process.
        extra_args: Additional CLI flags (e.g. ["--output-schema", "schema.json"]).

    Yields:
        Parsed JSON event dicts from Codex's JSONL output.

    Raises:
        CodexError: If the process exits with non-zero or times out.
    """
    cmd = [
        CODEX_BIN,
        "exec",
        "--json",
        "--full-auto",
    ]
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(prompt)

    log.info("Starting codex: %s (cwd=%s)", " ".join(cmd), workdir)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=workdir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        async with asyncio.timeout(timeout):
            async for raw_line in proc.stdout:
                line = raw_line.decode().strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    yield event
                except json.JSONDecodeError:
                    log.warning("Non-JSON line from codex: %s", line)

            await proc.wait()
    except asyncio.TimeoutError:
        log.error("Codex timed out after %ds, killing", timeout)
        proc.kill()
        await proc.wait()
        raise CodexError(f"Codex timed out after {timeout}s")

    if proc.returncode != 0:
        stderr_bytes = await proc.stderr.read()
        stderr_text = stderr_bytes.decode().strip()
        log.error("Codex exited with code %d: %s", proc.returncode, stderr_text)
        raise CodexError(
            f"Codex exited with code {proc.returncode}: {stderr_text}"
        )


async def run_codex_collect(
    prompt: str,
    workdir: str = ".",
    timeout: int = CODEX_TIMEOUT,
    extra_args: list[str] | None = None,
) -> CodexResult:
    """Run codex and collect all events into a CodexResult.

    Convenience wrapper over run_codex() for when you need the full
    result rather than streaming events.
    """
    result = CodexResult()
    try:
        async for event in run_codex(prompt, workdir, timeout, extra_args):
            result.events.append(event)
            # Extract final message from the last item.completed event
            if event.get("type") == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "agent_message":
                    content = item.get("content", [])
                    for part in content:
                        if part.get("type") == "text":
                            result.final_message = part.get("text", "")
    except CodexError as e:
        result.return_code = 1
        result.final_message = str(e)
    return result
