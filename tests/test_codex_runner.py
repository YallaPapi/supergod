"""Tests for codex_runner — mock subprocess to test event parsing."""

import asyncio
import json
import sys
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from supergod.worker.codex_runner import run_codex, run_codex_collect, CodexError, CodexResult


def _make_jsonl_script(events, exit_code=0):
    """Build a Python script that prints JSONL events to stdout, then exits."""
    lines = []
    for ev in events:
        lines.append(json.dumps(ev))
    joined = "\\n".join(lines)
    # Script that prints lines then exits
    return (
        f'import sys; sys.stdout.write("{joined}\\n"); sys.stdout.flush(); sys.exit({exit_code})'
    )


@pytest.fixture
def codex_bin(tmp_path):
    """Create a fake codex binary (Python script) that outputs controlled JSONL."""
    if sys.platform == "win32":
        script = tmp_path / "codex.bat"
    else:
        script = tmp_path / "codex"
    return script, tmp_path


def _write_fake_codex(script_path, events, exit_code=0, delay=0):
    """Write a fake codex script that outputs JSONL events."""
    lines_code = ""
    for ev in events:
        dumped = json.dumps(ev).replace("\\", "\\\\").replace('"', '\\"')
        lines_code += f'    print(\\"{dumped}\\")\n'

    if sys.platform == "win32":
        # Write a .bat that calls python inline
        py_lines = []
        for ev in events:
            py_lines.append(f"    print({json.dumps(json.dumps(ev))})")
        py_code = "import sys, time\\n"
        if delay:
            py_code += f"time.sleep({delay})\\n"
        for ev in events:
            safe = json.dumps(json.dumps(ev))
            py_code += f"print({safe})\\n"
        py_code += f"sys.exit({exit_code})"
        # Write as a .py file and a .bat wrapper
        py_path = script_path.parent / "fake_codex.py"
        with open(py_path, "w") as f:
            f.write("import sys, time\n")
            if delay:
                f.write(f"time.sleep({delay})\n")
            for ev in events:
                f.write(f"print({json.dumps(json.dumps(ev))})\n")
            f.write("sys.stdout.flush()\n")
            f.write(f"sys.exit({exit_code})\n")
        with open(script_path, "w") as f:
            f.write(f'@echo off\npython "{py_path}" %*\n')
    else:
        py_path = script_path
        with open(py_path, "w") as f:
            f.write("#!/usr/bin/env python3\n")
            f.write("import sys, time\n")
            if delay:
                f.write(f"time.sleep({delay})\n")
            for ev in events:
                f.write(f"print({json.dumps(json.dumps(ev))})\n")
            f.write("sys.stdout.flush()\n")
            f.write(f"sys.exit({exit_code})\n")
        import os
        os.chmod(str(py_path), 0o755)

    return str(script_path)


# --- Event parsing ---


async def test_run_codex_yields_events(codex_bin):
    """run_codex should yield parsed JSON events from subprocess stdout."""
    script_path, tmp = codex_bin
    events = [
        {"type": "thread.started", "id": "th1"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "content": [{"type": "text", "text": "done"}]}},
        {"type": "turn.completed"},
    ]
    fake_bin = _write_fake_codex(script_path, events)

    collected = []
    with patch("supergod.worker.codex_runner.CODEX_BIN", fake_bin):
        async for event in run_codex("test prompt", workdir=str(tmp)):
            collected.append(event)

    assert len(collected) == 4
    assert collected[0]["type"] == "thread.started"
    assert collected[2]["type"] == "item.completed"
    assert collected[3]["type"] == "turn.completed"


async def test_run_codex_collect(codex_bin):
    """run_codex_collect should gather all events and extract final_message."""
    script_path, tmp = codex_bin
    events = [
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "content": [{"type": "text", "text": "Hello world"}]}},
        {"type": "turn.completed"},
    ]
    fake_bin = _write_fake_codex(script_path, events)

    with patch("supergod.worker.codex_runner.CODEX_BIN", fake_bin):
        result = await run_codex_collect("test prompt", workdir=str(tmp))

    assert result.return_code == 0
    assert len(result.events) == 3
    assert result.final_message == "Hello world"


# --- Error handling ---


async def test_run_codex_nonzero_exit(codex_bin):
    """Non-zero exit code should raise CodexError."""
    script_path, tmp = codex_bin
    fake_bin = _write_fake_codex(script_path, [], exit_code=1)

    with patch("supergod.worker.codex_runner.CODEX_BIN", fake_bin):
        with pytest.raises(CodexError, match="exited with code 1"):
            async for _ in run_codex("test", workdir=str(tmp)):
                pass


async def test_run_codex_collect_handles_error(codex_bin):
    """run_codex_collect should catch CodexError and set return_code=1."""
    script_path, tmp = codex_bin
    fake_bin = _write_fake_codex(script_path, [], exit_code=1)

    with patch("supergod.worker.codex_runner.CODEX_BIN", fake_bin):
        result = await run_codex_collect("test", workdir=str(tmp))

    assert result.return_code == 1
    assert "exited with code 1" in result.final_message


# --- Timeout ---


async def test_run_codex_timeout(codex_bin):
    """Codex process exceeding timeout should raise CodexError."""
    script_path, tmp = codex_bin
    # Script that sleeps for 10 seconds
    fake_bin = _write_fake_codex(script_path, [], delay=10)

    with patch("supergod.worker.codex_runner.CODEX_BIN", fake_bin):
        with pytest.raises(CodexError, match="timed out"):
            async for _ in run_codex("test", workdir=str(tmp), timeout=1):
                pass


# --- Non-JSON lines ---


async def test_run_codex_skips_non_json(codex_bin):
    """Non-JSON lines from codex should be skipped without error."""
    script_path, tmp = codex_bin
    # Write a custom script that outputs mixed JSON and non-JSON
    if sys.platform == "win32":
        py_path = script_path.parent / "fake_codex.py"
        with open(py_path, "w") as f:
            f.write("import sys\n")
            f.write('print("Starting codex...")\n')
            f.write(f'print({json.dumps(json.dumps({"type": "turn.started"}))})\n')
            f.write('print("Some debug output")\n')
            f.write(f'print({json.dumps(json.dumps({"type": "turn.completed"}))})\n')
            f.write("sys.stdout.flush()\n")
        with open(script_path, "w") as f:
            f.write(f'@echo off\npython "{py_path}" %*\n')
    else:
        with open(script_path, "w") as f:
            f.write("#!/usr/bin/env python3\n")
            f.write('print("Starting codex...")\n')
            f.write(f'print({json.dumps(json.dumps({"type": "turn.started"}))})\n')
            f.write('print("Some debug output")\n')
            f.write(f'print({json.dumps(json.dumps({"type": "turn.completed"}))})\n')
        import os
        os.chmod(str(script_path), 0o755)

    fake_bin = str(script_path)

    collected = []
    with patch("supergod.worker.codex_runner.CODEX_BIN", fake_bin):
        async for event in run_codex("test", workdir=str(tmp)):
            collected.append(event)

    # Should only get the 2 valid JSON events
    assert len(collected) == 2
    assert collected[0]["type"] == "turn.started"
    assert collected[1]["type"] == "turn.completed"


# --- CodexResult dataclass ---


def test_codex_result_defaults():
    r = CodexResult()
    assert r.events == []
    assert r.final_message == ""
    assert r.return_code == 0


def test_codex_result_mutable_events():
    r = CodexResult()
    r.events.append({"type": "test"})
    # New instance should have fresh list
    r2 = CodexResult()
    assert r2.events == []
