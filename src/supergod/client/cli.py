"""Supergod CLI client -- send tasks and monitor progress from your laptop."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from functools import wraps
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import click
import websockets
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.spinner import Spinner
from rich.columns import Columns
from rich.markup import escape

from supergod.shared.config import ORCHESTRATOR_WS_URL, SUPERGOD_AUTH_TOKEN
from supergod.skills.importer import import_curated_agents
from supergod.shared.protocol import (
    ClientCancelMsg,
    ClientChatMsg,
    ClientPauseMsg,
    ClientResumeMsg,
    ClientStartFromBriefMsg,
    ClientStatusMsg,
    ClientTaskMsg,
    deserialize,
    new_id,
    serialize,
)

# stderr for status/decoration, stdout stays clean for piping
console = Console(stderr=True)
stdout_console = Console()


def async_command(f):
    """Decorator to run async Click commands."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return asyncio.run(f(*args, **kwargs))
        except KeyboardInterrupt:
            console.print("\n[dim]Interrupted.[/dim]")
            sys.exit(130)
    return wrapper


def _ws_url(server: str, path: str = "/ws/client") -> str:
    return _with_auth_token(f"{server}{path}")


def _with_auth_token(url: str) -> str:
    if not SUPERGOD_AUTH_TOKEN:
        return url
    parsed = urlparse(url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params.setdefault("token", SUPERGOD_AUTH_TOKEN)
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


def _connect_error(server: str, err: Exception):
    """Display a helpful connection error and exit."""
    console.print(Panel(
        f"[bold red]Connection failed[/bold red]\n\n"
        f"Server: [cyan]{server}[/cyan]\n"
        f"Error:  [dim]{escape(str(err))}[/dim]\n\n"
        f"[yellow]Troubleshooting:[/yellow]\n"
        f"  1. Is the orchestrator running?  [dim]supergod-orchestrator[/dim]\n"
        f"  2. Check the URL:  [dim]--server ws://host:port[/dim]\n"
        f"  3. Set env var:    [dim]export SUPERGOD_WS_URL=ws://host:port[/dim]",
        title="[red]Connection Error[/red]",
        border_style="red",
    ))
    sys.exit(1)


def _format_worker_table(workers) -> Table:
    """Build a Rich table of workers."""
    table = Table(title="Workers", show_lines=False, expand=True)
    table.add_column("Name", style="bold cyan", no_wrap=True)
    table.add_column("Status", no_wrap=True)

    for w in workers:
        if w.status == "idle":
            status = "[green]idle[/green]"
        elif w.status == "busy":
            status = "[yellow]busy[/yellow]"
        else:
            status = "[red]offline[/red]"
        table.add_row(w.name, status)

    if not workers:
        table.add_row("[dim]No workers connected[/dim]", "")

    return table


def _format_task_table(tasks) -> Table:
    """Build a Rich table of tasks."""
    table = Table(title="Tasks", show_lines=False, expand=True)
    table.add_column("Task ID", style="bold", no_wrap=True)
    table.add_column("Pri", no_wrap=True, justify="right")
    table.add_column("Status", no_wrap=True)
    table.add_column("Progress", no_wrap=True, justify="right")
    table.add_column("Prompt", max_width=50)

    status_colors = {
        "completed": "green",
        "failed": "red",
        "running": "yellow",
        "paused": "magenta",
        "pending": "dim",
        "decomposing": "blue",
        "assigned": "cyan",
        "cancelled": "dim red",
        "blocked": "bright_red",
    }

    for t in tasks:
        color = status_colors.get(t.status, "white")
        status_str = f"[{color}]{t.status}[/{color}]"
        progress = f"{t.completed_subtasks}/{t.subtasks}" if t.subtasks else "-"
        prompt_display = t.prompt[:50] + ("..." if len(t.prompt) > 50 else "")
        table.add_row(
            t.task_id, str(getattr(t, "priority", 100)), status_str, progress, escape(prompt_display)
        )

    if not tasks:
        table.add_row("[dim]No tasks[/dim]", "", "", "", "")

    return table


def _print_event_json(msg):
    """Print message as JSON to stdout for machine consumption."""
    try:
        data = msg.model_dump()
        click.echo(json.dumps(data, default=str))
    except Exception:
        click.echo(json.dumps({"type": "unknown", "raw": str(msg)}))


def _print_event_rich(msg):
    """Render a message from the orchestrator using Rich."""
    match msg.type:
        case "task_accepted":
            console.print(f"  [green]Task accepted:[/green] [bold]{msg.task_id}[/bold]")
        case "progress":
            worker = msg.worker or "orchestrator"
            output = msg.output
            if output:
                console.print(f"  [bold cyan][{worker}][/bold cyan] {escape(output)}")
        case "task_complete":
            console.print(Panel(
                f"[bold green]Completed[/bold green]\n{escape(msg.summary)}",
                title=f"Task {msg.task_id}",
                border_style="green",
            ))
        case "task_failed":
            console.print(Panel(
                f"[bold red]Failed[/bold red]\n{escape(msg.error)}",
                title=f"Task {msg.task_id}",
                border_style="red",
            ))
        case "chat_response":
            state = "ready" if msg.ready_to_start else "drafting"
            console.print(
                Panel(
                    escape(msg.reply),
                    title=f"[cyan]chat[/cyan] ({state})",
                    border_style="cyan",
                )
            )
        case "task_review":
            lines = [
                f"Completed: {msg.completed_count}",
                f"Failed/Cancelled: {msg.failed_count}",
                f"Blocked: {msg.blocked_count}",
                f"Tests: {msg.test_summary}",
            ]
            if msg.failed_subtasks:
                lines.append("\nFailed subtasks:")
                for s in msg.failed_subtasks[:10]:
                    lines.append(
                        f"- {s.get('subtask_id')}: {s.get('category')} | {s.get('error', '')[:120]}"
                    )
            if msg.blocked_subtasks:
                lines.append("\nBlocked subtasks:")
                for s in msg.blocked_subtasks[:10]:
                    lines.append(
                        f"- {s.get('subtask_id')}: {s.get('error', '')[:120]}"
                    )
            console.print(
                Panel(
                    escape("\n".join(lines)),
                    title=f"Task Review {msg.task_id}",
                    border_style="yellow",
                )
            )
        case "status_response":
            console.print()
            console.print(_format_worker_table(msg.workers))
            console.print()
            console.print(_format_task_table(msg.tasks))
        case "workers":
            console.print()
            console.print(_format_worker_table(msg.list))


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.option("--json", "use_json", is_flag=True, help="Output machine-readable JSON")
@click.option(
    "--server", "-s",
    default=ORCHESTRATOR_WS_URL,
    envvar="SUPERGOD_WS_URL",
    help="Orchestrator WebSocket URL [env: SUPERGOD_WS_URL]",
)
@click.pass_context
def cli(ctx, use_json: bool, server: str):
    """Supergod -- Multi-agent orchestration for Codex CLI."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = use_json
    ctx.obj["server"] = server


# ---------------------------------------------------------------------------
# run -- send a task and stream live output
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("prompt")
@click.option(
    "--priority",
    default=100,
    type=int,
    show_default=True,
    help="Lower number = higher priority (0-1000).",
)
@click.pass_context
@async_command
async def run(ctx, prompt: str, priority: int):
    """Send a task to the orchestrator and stream progress."""
    server = ctx.obj["server"]
    use_json = ctx.obj["json"]
    ws_url = _ws_url(server)

    if not use_json:
        console.print(Panel(
            f"[bold]{escape(prompt)}[/bold]",
            title="[magenta]supergod[/magenta] run",
            border_style="magenta",
            subtitle=f"[dim]{server}[/dim]",
        ))

    try:
        async with websockets.connect(ws_url) as ws:
            msg = ClientTaskMsg(prompt=prompt, priority=priority)
            await ws.send(serialize(msg))

            if not use_json:
                console.print(f"  [blue]Sent task:[/blue] [bold]{msg.task_id}[/bold]\n")

            async for raw in ws:
                try:
                    response = deserialize(raw)
                except ValueError:
                    continue

                if use_json:
                    _print_event_json(response)
                else:
                    _print_event_rich(response)

                if response.type in ("task_complete", "task_failed"):
                    code = 0 if response.type == "task_complete" else 1
                    sys.exit(code)

    except websockets.exceptions.WebSocketException as e:
        if use_json:
            click.echo(json.dumps({"type": "error", "error": str(e)}))
            sys.exit(1)
        _connect_error(server, e)
    except OSError as e:
        if use_json:
            click.echo(json.dumps({"type": "error", "error": str(e)}))
            sys.exit(1)
        _connect_error(server, e)


# ---------------------------------------------------------------------------
# status -- one-shot status query
# ---------------------------------------------------------------------------

@cli.command()
@click.pass_context
@async_command
async def status(ctx):
    """Show current task and worker status."""
    server = ctx.obj["server"]
    use_json = ctx.obj["json"]
    ws_url = _ws_url(server)

    try:
        async with websockets.connect(ws_url) as ws:
            await ws.send(serialize(ClientStatusMsg()))
            raw = await ws.recv()
            response = deserialize(raw)

            if use_json:
                _print_event_json(response)
            else:
                if not use_json:
                    console.print(Panel.fit(
                        f"[dim]{server}[/dim]",
                        title="[magenta]supergod[/magenta] status",
                        border_style="magenta",
                    ))
                _print_event_rich(response)

    except websockets.exceptions.WebSocketException as e:
        if use_json:
            click.echo(json.dumps({"type": "error", "error": str(e)}))
            sys.exit(1)
        _connect_error(server, e)
    except OSError as e:
        if use_json:
            click.echo(json.dumps({"type": "error", "error": str(e)}))
            sys.exit(1)
        _connect_error(server, e)


# ---------------------------------------------------------------------------
# workers -- list connected workers
# ---------------------------------------------------------------------------

@cli.command()
@click.pass_context
@async_command
async def workers(ctx):
    """List connected workers and their status."""
    server = ctx.obj["server"]
    use_json = ctx.obj["json"]
    ws_url = _ws_url(server)

    try:
        async with websockets.connect(ws_url) as ws:
            await ws.send(serialize(ClientStatusMsg()))
            raw = await ws.recv()
            response = deserialize(raw)

            if use_json:
                workers_data = [
                    {"name": w.name, "status": w.status}
                    for w in getattr(response, "workers", [])
                ]
                click.echo(json.dumps({"workers": workers_data}, default=str))
            else:
                console.print(Panel.fit(
                    f"[dim]{server}[/dim]",
                    title="[magenta]supergod[/magenta] workers",
                    border_style="magenta",
                ))
                worker_list = getattr(response, "workers", [])
                console.print()
                console.print(_format_worker_table(worker_list))

                idle = sum(1 for w in worker_list if w.status == "idle")
                busy = sum(1 for w in worker_list if w.status == "busy")
                total = len(worker_list)
                console.print(
                    f"\n  [dim]Total: {total}  |  "
                    f"Idle: [green]{idle}[/green]  |  "
                    f"Busy: [yellow]{busy}[/yellow][/dim]"
                )

    except websockets.exceptions.WebSocketException as e:
        if use_json:
            click.echo(json.dumps({"type": "error", "error": str(e)}))
            sys.exit(1)
        _connect_error(server, e)
    except OSError as e:
        if use_json:
            click.echo(json.dumps({"type": "error", "error": str(e)}))
            sys.exit(1)
        _connect_error(server, e)


# ---------------------------------------------------------------------------
# cancel -- cancel a running task
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("task_id")
@click.pass_context
@async_command
async def cancel(ctx, task_id: str):
    """Cancel a running task by its ID."""
    server = ctx.obj["server"]
    use_json = ctx.obj["json"]
    ws_url = _ws_url(server)

    try:
        async with websockets.connect(ws_url) as ws:
            await ws.send(serialize(ClientCancelMsg(task_id=task_id)))
            if use_json:
                click.echo(json.dumps({"type": "cancelled", "task_id": task_id}))
            else:
                console.print(f"  [yellow]Cancelled[/yellow] task [bold]{task_id}[/bold]")

    except websockets.exceptions.WebSocketException as e:
        if use_json:
            click.echo(json.dumps({"type": "error", "error": str(e)}))
            sys.exit(1)
        _connect_error(server, e)
    except OSError as e:
        if use_json:
            click.echo(json.dumps({"type": "error", "error": str(e)}))
            sys.exit(1)
        _connect_error(server, e)


@cli.command()
@click.argument("task_id")
@click.pass_context
@async_command
async def pause(ctx, task_id: str):
    """Pause a task (stops assigning new subtasks)."""
    server = ctx.obj["server"]
    use_json = ctx.obj["json"]
    ws_url = _ws_url(server)

    try:
        async with websockets.connect(ws_url) as ws:
            await ws.send(serialize(ClientPauseMsg(task_id=task_id)))
            if use_json:
                click.echo(json.dumps({"type": "paused", "task_id": task_id}))
            else:
                console.print(f"  [yellow]Paused[/yellow] task [bold]{task_id}[/bold]")
    except websockets.exceptions.WebSocketException as e:
        if use_json:
            click.echo(json.dumps({"type": "error", "error": str(e)}))
            sys.exit(1)
        _connect_error(server, e)
    except OSError as e:
        if use_json:
            click.echo(json.dumps({"type": "error", "error": str(e)}))
            sys.exit(1)
        _connect_error(server, e)


@cli.command()
@click.argument("task_id")
@click.pass_context
@async_command
async def resume(ctx, task_id: str):
    """Resume a paused task."""
    server = ctx.obj["server"]
    use_json = ctx.obj["json"]
    ws_url = _ws_url(server)

    try:
        async with websockets.connect(ws_url) as ws:
            await ws.send(serialize(ClientResumeMsg(task_id=task_id)))
            if use_json:
                click.echo(json.dumps({"type": "resumed", "task_id": task_id}))
            else:
                console.print(f"  [green]Resumed[/green] task [bold]{task_id}[/bold]")
    except websockets.exceptions.WebSocketException as e:
        if use_json:
            click.echo(json.dumps({"type": "error", "error": str(e)}))
            sys.exit(1)
        _connect_error(server, e)
    except OSError as e:
        if use_json:
            click.echo(json.dumps({"type": "error", "error": str(e)}))
            sys.exit(1)
        _connect_error(server, e)


# ---------------------------------------------------------------------------
# chat -- interactive brief + explicit start
# ---------------------------------------------------------------------------

@cli.command()
@click.pass_context
@async_command
async def chat(ctx):
    """Interactive planning chat before starting execution."""
    server = ctx.obj["server"]
    use_json = ctx.obj["json"]
    ws_url = _ws_url(server)
    session_id = new_id()

    if use_json:
        click.echo(json.dumps({"type": "chat_session", "session_id": session_id}))
    else:
        console.print(Panel(
            "Chat mode:\n"
            "- Describe goal\n"
            "- Describe constraints/context\n"
            "- Describe acceptance criteria\n"
            "- Type /start to run, /reset for new session, /quit to exit",
            title="[magenta]supergod[/magenta] chat",
            border_style="magenta",
            subtitle=f"[dim]{server}[/dim]",
        ))

    try:
        async with websockets.connect(ws_url) as ws:
            while True:
                user_input = (await asyncio.to_thread(input, "you> ")).strip()
                if not user_input:
                    continue
                if user_input.lower() in {"/quit", "quit", "exit"}:
                    return
                if user_input.lower() == "/reset":
                    session_id = new_id()
                    if use_json:
                        click.echo(json.dumps({"type": "chat_session", "session_id": session_id}))
                    else:
                        console.print(f"[dim]Started new session: {session_id}[/dim]")
                    continue

                if user_input.lower() in {"/start", "start"}:
                    await ws.send(
                        serialize(ClientStartFromBriefMsg(session_id=session_id))
                    )
                    accepted_task_id = None
                    while True:
                        raw = await ws.recv()
                        response = deserialize(raw)
                        if use_json:
                            _print_event_json(response)
                        else:
                            _print_event_rich(response)
                        if response.type == "task_accepted":
                            accepted_task_id = response.task_id
                        if response.type in ("task_complete", "task_failed"):
                            if not accepted_task_id or response.task_id == accepted_task_id:
                                break
                    continue

                await ws.send(
                    serialize(
                        ClientChatMsg(
                            session_id=session_id,
                            message=user_input,
                        )
                    )
                )
                while True:
                    raw = await ws.recv()
                    response = deserialize(raw)
                    if use_json:
                        _print_event_json(response)
                    else:
                        _print_event_rich(response)
                    if response.type == "chat_response":
                        break

    except websockets.exceptions.WebSocketException as e:
        if use_json:
            click.echo(json.dumps({"type": "error", "error": str(e)}))
            sys.exit(1)
        _connect_error(server, e)
    except OSError as e:
        if use_json:
            click.echo(json.dumps({"type": "error", "error": str(e)}))
            sys.exit(1)
        _connect_error(server, e)


# ---------------------------------------------------------------------------
# watch -- persistent monitoring with live refresh
# ---------------------------------------------------------------------------

@cli.command()
@click.option(
    "--interval", "-i",
    default=5,
    type=int,
    help="Refresh interval in seconds",
)
@click.pass_context
@async_command
async def watch(ctx, interval: int):
    """Live-refresh dashboard of workers and tasks. Press Ctrl+C to stop."""
    server = ctx.obj["server"]
    use_json = ctx.obj["json"]
    ws_url = _ws_url(server)

    if use_json:
        console.print("[red]Watch mode does not support --json output.[/red]", style="bold")
        sys.exit(1)

    def _build_dashboard(response, ts: str) -> Panel:
        """Build the dashboard panel from a status response."""
        worker_table = _format_worker_table(
            getattr(response, "workers", [])
        )
        task_table = _format_task_table(
            getattr(response, "tasks", [])
        )

        from rich.layout import Layout
        layout = Layout()
        layout.split_column(
            Layout(worker_table, name="workers", ratio=1),
            Layout(task_table, name="tasks", ratio=2),
        )

        return Panel(
            layout,
            title=f"[magenta]supergod[/magenta] watch [dim]({server})[/dim]",
            subtitle=f"[dim]Updated {ts}  |  Ctrl+C to exit  |  Refresh: {interval}s[/dim]",
            border_style="magenta",
        )

    # Initial loading screen
    loading = Panel(
        "[dim]Connecting...[/dim]",
        title="[magenta]supergod[/magenta] watch",
        border_style="magenta",
    )

    with Live(loading, console=console, refresh_per_second=2) as live:
        while True:
            try:
                async with websockets.connect(ws_url) as ws:
                    await ws.send(serialize(ClientStatusMsg()))
                    raw = await ws.recv()
                    response = deserialize(raw)
                    ts = datetime.now().strftime("%H:%M:%S")
                    live.update(_build_dashboard(response, ts))

            except (websockets.exceptions.WebSocketException, OSError):
                ts = datetime.now().strftime("%H:%M:%S")
                live.update(Panel(
                    "[red]Cannot reach orchestrator. Retrying...[/red]",
                    title=f"[magenta]supergod[/magenta] watch [dim]({server})[/dim]",
                    subtitle=f"[dim]{ts}[/dim]",
                    border_style="red",
                ))

            await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# import-skills -- ingest curated external agent files into local library
# ---------------------------------------------------------------------------

@cli.command("import-skills")
@click.option(
    "--source",
    required=True,
    help="Path to external .claude/agents directory.",
)
@click.option(
    "--exclude-project-specific",
    is_flag=True,
    help="Skip project-specific packs (for generic library only).",
)
@click.pass_context
def import_skills(ctx, source: str, exclude_project_specific: bool):
    """Import curated external agents into Supergod skill library."""
    use_json = ctx.obj["json"]
    index = import_curated_agents(
        source_dir=source,
        include_project_specific=not exclude_project_specific,
    )
    stats = index.get("stats", {})
    payload = {
        "type": "skills_imported",
        "total_skills": int(stats.get("total_skills", 0)),
        "missing_skills": int(stats.get("missing_skills", 0)),
    }
    if use_json:
        click.echo(json.dumps(payload))
        return
    console.print(
        Panel(
            f"Imported skills: [bold]{payload['total_skills']}[/bold]\n"
            f"Missing curated skills: [bold]{payload['missing_skills']}[/bold]",
            title="[magenta]supergod[/magenta] import-skills",
            border_style="magenta",
        )
    )


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------

def main():
    cli()


if __name__ == "__main__":
    main()
