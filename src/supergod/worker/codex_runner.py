"""Run Codex CLI in non-interactive mode and stream JSONL events.

This is the core building block used by both workers (to execute tasks)
and the orchestrator (to think — decompose tasks, evaluate results).
"""

import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from typing import AsyncGenerator, Optional

from supergod.shared.config import CODEX_BIN, CODEX_TIMEOUT, CODEX_SANDBOX

log = logging.getLogger(__name__)


class CodexError(Exception):
    pass


class CodexTimeoutError(CodexError):
    pass


@dataclass
class CodexResult:
    events: list[dict] = field(default_factory=list)
    final_message: str = ""
    return_code: int = 0
    session_id: Optional[str] = None
    stderr_output: str = ""


async def _drain_stderr(
    stream: asyncio.StreamReader, lines: list[str]
) -> None:
    """Read stderr lines in background so the pipe doesn't block."""
    try:
        async for raw_line in stream:
            line = raw_line.decode(errors="replace").rstrip()
            if line:
                lines.append(line)
                log.debug("codex stderr: %s", line)
    except Exception:
        pass


async def _kill_proc(proc: asyncio.subprocess.Process) -> None:
    """Terminate a subprocess, escalate to kill if it won't die."""
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
    except ProcessLookupError:
        pass


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
        extra_args: Additional CLI flags (e.g. ["--output-schema", "..."]).

    Yields:
        Parsed JSON event dicts from Codex's JSONL output.

    Raises:
        CodexTimeoutError: If the process exceeds the timeout.
        CodexError: If the process exits with non-zero.
    """
    cmd = [
        CODEX_BIN,
        "exec",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
    ]
    if extra_args:
        cmd.extend(extra_args)
    # Use stdin ("-") to pass prompt — avoids shell quoting issues on Windows
    cmd.append("-")

    log.info("Starting codex (cwd=%s, timeout=%ds): %s", workdir, timeout, prompt[:200])

    # On Windows, .cmd/.bat files must be run via cmd.exe
    if sys.platform == "win32" and CODEX_BIN.endswith((".cmd", ".bat")):
        cmd = ["cmd", "/c"] + cmd

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=workdir,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # Write prompt to stdin and close it
    proc.stdin.write(prompt.encode())
    await proc.stdin.drain()
    proc.stdin.close()

    # Drain stderr in background so it doesn't fill the pipe buffer
    stderr_lines: list[str] = []
    stderr_task = asyncio.create_task(_drain_stderr(proc.stderr, stderr_lines))

    # Buffer for partial JSON lines (in case a line arrives split across reads)
    buffer = ""

    try:
        async with asyncio.timeout(timeout):
            async for raw_chunk in proc.stdout:
                buffer += raw_chunk.decode(errors="replace")
                # Process all complete lines in the buffer
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        yield event
                    except json.JSONDecodeError:
                        log.warning("Non-JSON line from codex: %s", line[:500])

            # Process any remaining content in buffer after stdout closes
            remaining = buffer.strip()
            if remaining:
                try:
                    event = json.loads(remaining)
                    yield event
                except json.JSONDecodeError:
                    log.warning("Non-JSON trailing content: %s", remaining[:500])

            await proc.wait()

    except asyncio.TimeoutError:
        log.error("Codex timed out after %ds, killing", timeout)
        await _kill_proc(proc)
        raise CodexTimeoutError(f"Codex timed out after {timeout}s")

    except asyncio.CancelledError:
        log.warning("Codex run cancelled, killing subprocess")
        await _kill_proc(proc)
        raise

    finally:
        # Always clean up the subprocess
        await _kill_proc(proc)
        stderr_task.cancel()
        try:
            await stderr_task
        except asyncio.CancelledError:
            pass

    if proc.returncode != 0:
        stderr_text = "\n".join(stderr_lines)
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

            event_type = event.get("type", "")

            # Capture session ID from thread.started
            if event_type == "thread.started" and result.session_id is None:
                result.session_id = event.get("session_id")

            # Extract final message from item.completed events
            if event_type == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "agent_message":
                    # Direct text field (observed in codex exec output)
                    if "text" in item:
                        result.final_message = item["text"]
                    # Nested content array format
                    elif "content" in item and isinstance(item["content"], list):
                        for part in item["content"]:
                            if isinstance(part, dict) and part.get("type") == "text":
                                result.final_message = part.get("text", "")
                    # Direct content string
                    elif "content" in item and isinstance(item["content"], str):
                        result.final_message = item["content"]

            # Flag failures (but ignore transient reconnection errors)
            if event_type in ("turn.failed", "error"):
                error_msg = event.get("error", event.get("message", "unknown error"))
                is_reconnect = "Reconnecting" in error_msg or "Falling back" in error_msg
                if is_reconnect:
                    log.warning("Codex transient: %s", error_msg)
                else:
                    log.error("Codex reported %s: %s", event_type, error_msg)
                    result.return_code = 1
                    if not result.final_message:
                        result.final_message = f"Codex {event_type}: {error_msg}"

    except CodexError as e:
        result.return_code = 1
        result.final_message = str(e)
    return result


async def resume_codex(
    session_id: str,
    prompt: str,
    workdir: str = ".",
    timeout: int = CODEX_TIMEOUT,
) -> AsyncGenerator[dict, None]:
    """Resume a previous codex session with a new instruction.

    Args:
        session_id: The session_id from a previous thread.started event.
        prompt: Follow-up instruction.
        workdir: Working directory.
        timeout: Max seconds.

    Yields:
        Parsed JSON event dicts.
    """
    cmd = [
        CODEX_BIN,
        "exec",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "resume",
        session_id,
        prompt,
    ]

    log.info("Resuming codex session %s: %s", session_id, prompt[:100])

    # On Windows, .cmd/.bat files must be run via cmd.exe
    if sys.platform == "win32" and CODEX_BIN.endswith((".cmd", ".bat")):
        cmd = ["cmd", "/c"] + cmd

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=workdir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stderr_lines: list[str] = []
    stderr_task = asyncio.create_task(_drain_stderr(proc.stderr, stderr_lines))
    buffer = ""

    try:
        async with asyncio.timeout(timeout):
            async for raw_chunk in proc.stdout:
                buffer += raw_chunk.decode(errors="replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        log.warning("Non-JSON line from codex resume: %s", line[:500])

            remaining = buffer.strip()
            if remaining:
                try:
                    yield json.loads(remaining)
                except json.JSONDecodeError:
                    pass

            await proc.wait()

    except asyncio.TimeoutError:
        await _kill_proc(proc)
        raise CodexTimeoutError(f"Codex resume timed out after {timeout}s")
    except asyncio.CancelledError:
        await _kill_proc(proc)
        raise
    finally:
        await _kill_proc(proc)
        stderr_task.cancel()
        try:
            await stderr_task
        except asyncio.CancelledError:
            pass

    if proc.returncode != 0:
        stderr_text = "\n".join(stderr_lines)
        raise CodexError(f"Codex resume exited {proc.returncode}: {stderr_text}")
