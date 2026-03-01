#!/usr/bin/env python3
"""Cluster deployment helper.

This script eliminates ad-hoc shell quoting by:
- Running Linux commands through `bash -s` with script stdin.
- Running Windows commands through PowerShell `-EncodedCommand`.
- Keeping deployment actions idempotent and repeatable.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


def run(
    cmd: list[str],
    *,
    input_text: str | None = None,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=capture_output,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(cmd)}\n"
            f"{result.stdout}\n{result.stderr}"
        )
    return result


def run_windows_ps(host: str, script: str) -> str:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            host,
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded,
        ],
        capture_output=True,
    )
    return (result.stdout or "").strip()


def run_linux_bash(host: str, script: str) -> str:
    normalized = script.replace("\r\n", "\n").replace("\r", "")
    result = run(
        ["ssh", "-o", "BatchMode=yes", host, "bash", "-s"],
        input_text=normalized,
        capture_output=True,
    )
    return (result.stdout or "").strip()


def sync_windows_source(host: str, local_root: Path, remote_repo: str) -> None:
    src_dir = local_root / "src" / "supergod"
    pyproject = local_root / "pyproject.toml"
    remote_scp_repo = "/" + remote_repo.replace("\\", "/")
    remote_src_parent = f"{remote_scp_repo}/src/"
    run(["scp", "-O", "-r", str(src_dir), f"{host}:{remote_src_parent}"])
    run(["scp", "-O", str(pyproject), f"{host}:{remote_scp_repo}/pyproject.toml"])


def sync_linux_source(host: str, local_root: Path, remote_repo: str) -> None:
    src_dir = local_root / "src" / "supergod"
    pyproject = local_root / "pyproject.toml"
    run(["scp", "-r", str(src_dir), f"{host}:{remote_repo}/src/"])
    run(["scp", str(pyproject), f"{host}:{remote_repo}/pyproject.toml"])


def restart_windows_cluster(host: str) -> str:
    script = r"""
$ErrorActionPreference = "Continue"
$tasks = @("SupergodOrch", "SupergodWorker", "SupergodWorker2", "SupergodWorker3")
$targets = Get-CimInstance Win32_Process | Where-Object {
  $_.CommandLine -and (
    $_.CommandLine -match "supergod\.orchestrator\.server" -or
    $_.CommandLine -match "supergod\.worker\.daemon" -or
    $_.CommandLine -match "C:\\supergod\\start_.*\.bat"
  )
}
foreach ($p in $targets) {
  try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {}
}
Start-Sleep -Seconds 2
foreach ($t in $tasks) {
  schtasks /run /tn $t | Out-Null
}
Start-Sleep -Seconds 3
Get-CimInstance Win32_Process | Where-Object {
  $_.CommandLine -and (
    $_.CommandLine -match "supergod\.orchestrator\.server" -or
    $_.CommandLine -match "supergod\.worker\.daemon"
  )
} | Select-Object ProcessId,Name,CommandLine | Sort-Object ProcessId | ConvertTo-Json -Depth 3
"""
    return run_windows_ps(host, script)


def restart_linux_workers(host: str, remote_repo: str) -> str:
    script = f"""#!/usr/bin/env bash
set -eu
pkill -f '[s]upergod.worker.daemon' || true
sleep 1
{remote_repo}/start_workers.sh
sleep 2
pgrep -af supergod.worker.daemon || true
"""
    return run_linux_bash(host, script)


def http_get_status(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def get_snapshot(url_base: str) -> dict:
    with urllib.request.urlopen(f"{url_base.rstrip('/')}/snapshot", timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def cmd_deploy(args: argparse.Namespace) -> int:
    local_root = Path(args.local_root).resolve()
    sync_windows_source(args.orchestrator_host, local_root, args.orchestrator_repo)
    sync_linux_source(args.worker_host, local_root, args.worker_repo)

    win_result = restart_windows_cluster(args.orchestrator_host)
    linux_result = restart_linux_workers(args.worker_host, args.worker_repo)

    print("Windows restart result:")
    print(win_result)
    print("Linux restart result:")
    print(linux_result)
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    base = args.orchestrator_http
    health = http_get_status(f"{base.rstrip('/')}/healthz")
    mission = http_get_status(f"{base.rstrip('/')}/mission")
    snapshot = get_snapshot(base)
    workers = snapshot.get("workers", [])
    tasks = snapshot.get("tasks", [])
    print(
        json.dumps(
            {
                "healthz": health,
                "mission": mission,
                "workers_total": len(workers),
                "workers_idle": len([w for w in workers if w.get("status") == "idle"]),
                "recent_tasks": [t.get("task_id") for t in tasks[:5]],
            },
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Supergod cluster deploy helper")
    sub = parser.add_subparsers(dest="command", required=True)

    deploy = sub.add_parser("deploy", help="Sync code to both servers and restart daemons")
    deploy.add_argument("--local-root", default=".", help="Local repo root")
    deploy.add_argument("--orchestrator-host", default="admin@88.99.142.89")
    deploy.add_argument("--orchestrator-repo", default="C:/supergod")
    deploy.add_argument("--worker-host", default="root@77.42.67.96")
    deploy.add_argument("--worker-repo", default="/opt/supergod")
    deploy.set_defaults(func=cmd_deploy)

    health = sub.add_parser("health", help="Check live dashboard/API health")
    health.add_argument("--orchestrator-http", default="http://88.99.142.89:8080")
    health.set_defaults(func=cmd_health)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
