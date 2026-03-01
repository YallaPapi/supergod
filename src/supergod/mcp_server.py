"""Supergod MCP server — exposes supergod as tools for Claude Code / any MCP client."""

import asyncio
import json
import logging
import os
import sys
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import websockets

from mcp.server.fastmcp import FastMCP

# Logging to stderr only — stdout is reserved for MCP protocol
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("supergod.mcp")

ORCHESTRATOR_URL = os.getenv("SUPERGOD_WS_URL", "ws://88.99.142.89:8080")
AUTH_TOKEN = os.getenv("SUPERGOD_AUTH_TOKEN", "")

mcp = FastMCP("supergod")


async def _send_and_stream(prompt: str, timeout: int = 600) -> str:
    """Connect to orchestrator, submit task, collect all progress until completion.

    Uses ping_interval to keep connection alive and polls status on disconnect.
    """
    ws_url = _with_auth_token(f"{ORCHESTRATOR_URL}/ws/client")
    results = []
    task_id = None

    try:
        async with websockets.connect(
            ws_url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            # Send task
            task_msg = json.dumps({"type": "task", "prompt": prompt})
            await ws.send(task_msg)

            deadline = asyncio.get_event_loop().time() + timeout
            async for raw in ws:
                if asyncio.get_event_loop().time() > deadline:
                    return "Task timed out after {} seconds".format(timeout)

                data = json.loads(raw)
                msg_type = data.get("type", "")

                if msg_type == "task_accepted":
                    task_id = data.get("task_id", "")
                    results.append(f"Task accepted: {task_id}")

                elif msg_type == "progress":
                    output = data.get("output", "")
                    if output:
                        results.append(output)

                elif msg_type == "task_complete":
                    summary = data.get("summary", "Completed")
                    results.append(f"\nTask completed: {summary}")
                    return "\n".join(results)

                elif msg_type == "task_failed":
                    error = data.get("error", "Unknown error")
                    results.append(f"\nTask failed: {error}")
                    return "\n".join(results)

    except Exception as e:
        logger.warning(f"WebSocket disconnected: {e}")

    # If we got disconnected, poll status until task finishes
    if task_id:
        results.append("[connection lost, polling for completion...]")
        for _ in range(60):  # poll for up to 5 minutes
            await asyncio.sleep(5)
            try:
                status = await _get_task_status(task_id)
                if status == "completed":
                    results.append("\nTask completed (confirmed via status poll)")
                    return "\n".join(results)
                elif status == "failed":
                    results.append("\nTask failed (confirmed via status poll)")
                    return "\n".join(results)
            except Exception:
                continue
        results.append("\nTimed out waiting for task completion")

    return "\n".join(results) if results else "Connection failed before task was submitted"


async def _get_task_status(task_id: str) -> str:
    """Quick status check for a specific task."""
    ws_url = _with_auth_token(f"{ORCHESTRATOR_URL}/ws/client")
    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"type": "status"}))
        raw = await ws.recv()
        data = json.loads(raw)
        for t in data.get("tasks", []):
            if t.get("task_id") == task_id:
                return t.get("status", "unknown")
    return "unknown"


async def _get_status() -> str:
    """Query orchestrator for current status."""
    ws_url = _with_auth_token(f"{ORCHESTRATOR_URL}/ws/client")
    try:
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps({"type": "status"}))
            raw = await ws.recv()
            data = json.loads(raw)

            lines = []

            # Workers
            workers = data.get("workers", [])
            lines.append(f"Workers ({len(workers)}):")
            for w in workers:
                lines.append(f"  {w['name']}: {w['status']}")

            # Tasks
            tasks = data.get("tasks", [])
            if tasks:
                lines.append(f"\nTasks ({len(tasks)}):")
                for t in tasks:
                    progress = f"{t.get('completed_subtasks', 0)}/{t.get('subtasks', 0)}"
                    prompt = t.get("prompt", "")[:80]
                    lines.append(f"  [{t['status']}] {t['task_id']} ({progress}) {prompt}")
            else:
                lines.append("\nNo active tasks.")

            return "\n".join(lines)

    except Exception as e:
        return f"Cannot reach orchestrator at {ORCHESTRATOR_URL}: {e}"


@mcp.tool()
async def supergod_run(task: str) -> str:
    """Send a task to supergod for parallel execution across multiple AI workers.

    Supergod decomposes the task into subtasks, distributes them across
    multiple servers running Codex CLI, and returns the merged result.
    Use this for any task that benefits from parallelization: building
    multi-file projects, research across multiple topics, data processing, etc.

    Args:
        task: Natural language description of what to build or do.
    """
    logger.info(f"supergod_run called: {task[:100]}...")
    return await _send_and_stream(task)


@mcp.tool()
async def supergod_status() -> str:
    """Check the current status of supergod workers and tasks.

    Returns a list of connected workers (idle/busy) and any active tasks
    with their progress.
    """
    logger.info("supergod_status called")
    return await _get_status()


def main():
    mcp.run(transport="stdio")


def _with_auth_token(url: str) -> str:
    if not AUTH_TOKEN:
        return url
    parsed = urlparse(url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params.setdefault("token", AUTH_TOKEN)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(params),
            parsed.fragment,
        )
    )


if __name__ == "__main__":
    main()
