"""Supergod CLI client — send tasks and monitor progress from your laptop."""

from __future__ import annotations

import asyncio
import json
import sys

import click
import websockets

from supergod.shared.config import ORCHESTRATOR_WS_URL
from supergod.shared.protocol import (
    ClientCancelMsg,
    ClientStatusMsg,
    ClientTaskMsg,
    deserialize,
    serialize,
)


def _print_event(msg):
    """Pretty-print a message from the orchestrator."""
    match msg.type:
        case "task_accepted":
            click.echo(click.style(f"  Task accepted: {msg.task_id}", fg="green"))
        case "progress":
            worker = msg.worker or "orchestrator"
            output = msg.output
            if output:
                click.echo(click.style(f"  [{worker}] ", fg="cyan", bold=True) + output)
        case "task_complete":
            click.echo(click.style(f"\n  DONE: {msg.summary}", fg="green", bold=True))
        case "task_failed":
            click.echo(click.style(f"\n  FAILED: {msg.error}", fg="red", bold=True))
        case "status_response":
            click.echo(click.style("\n  Workers:", fg="yellow", bold=True))
            for w in msg.workers:
                color = "green" if w.status == "idle" else "yellow" if w.status == "busy" else "red"
                click.echo(f"    {click.style(w.name, fg=color)}: {w.status}")
            click.echo(click.style("\n  Tasks:", fg="yellow", bold=True))
            if not msg.tasks:
                click.echo("    (none)")
            for t in msg.tasks:
                color = {"completed": "green", "failed": "red", "running": "yellow"}.get(t.status, "white")
                progress = f"({t.completed_subtasks}/{t.subtasks})" if t.subtasks else ""
                click.echo(
                    f"    {click.style(t.task_id, fg=color)}: "
                    f"{t.status} {progress} — {t.prompt[:60]}"
                )
        case "workers":
            for w in msg.list:
                click.echo(f"  {w.name}: {w.status}")


async def _run_task(url: str, prompt: str):
    """Connect, send a task, and stream output until complete."""
    ws_url = f"{url}/ws/client"
    click.echo(click.style(f"  Connecting to {ws_url}...", fg="blue"))

    async with websockets.connect(ws_url) as ws:
        # Send task
        msg = ClientTaskMsg(prompt=prompt)
        await ws.send(serialize(msg))
        click.echo(click.style(f"  Sent task: {msg.task_id}", fg="blue"))

        # Listen for responses
        try:
            async for raw in ws:
                try:
                    response = deserialize(raw)
                    _print_event(response)

                    # Exit on terminal states
                    if response.type in ("task_complete", "task_failed"):
                        return
                except ValueError:
                    pass
        except KeyboardInterrupt:
            click.echo("\n  Interrupted. Task continues on server.")


async def _get_status(url: str):
    ws_url = f"{url}/ws/client"
    async with websockets.connect(ws_url) as ws:
        await ws.send(serialize(ClientStatusMsg()))
        raw = await ws.recv()
        msg = deserialize(raw)
        _print_event(msg)


@click.group()
def cli():
    """Supergod — Multi-agent orchestration for Codex CLI."""
    pass


@cli.command()
@click.argument("prompt")
@click.option(
    "--server",
    default=ORCHESTRATOR_WS_URL,
    envvar="SUPERGOD_WS_URL",
    help="Orchestrator WebSocket URL",
)
def run(prompt: str, server: str):
    """Send a task to the orchestrator and stream progress."""
    click.echo(click.style("\n  supergod", fg="magenta", bold=True))
    click.echo(click.style("  ─" * 30, fg="magenta"))
    asyncio.run(_run_task(server, prompt))


@cli.command()
@click.option(
    "--server",
    default=ORCHESTRATOR_WS_URL,
    envvar="SUPERGOD_WS_URL",
    help="Orchestrator WebSocket URL",
)
def status(server: str):
    """Check current task and worker status."""
    click.echo(click.style("\n  supergod status", fg="magenta", bold=True))
    asyncio.run(_get_status(server))


@cli.command()
@click.argument("task_id")
@click.option(
    "--server",
    default=ORCHESTRATOR_WS_URL,
    envvar="SUPERGOD_WS_URL",
)
def cancel(task_id: str, server: str):
    """Cancel a running task."""

    async def _cancel():
        ws_url = f"{server}/ws/client"
        async with websockets.connect(ws_url) as ws:
            await ws.send(serialize(ClientCancelMsg(task_id=task_id)))
            click.echo(click.style(f"  Cancelled {task_id}", fg="yellow"))

    asyncio.run(_cancel())


def main():
    cli()


if __name__ == "__main__":
    main()
