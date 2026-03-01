# Ops Hardening Plan

This document defines the permanent fix for two issues:
1. Shell quoting fragility when running remote commands.
2. Version drift across orchestrator/worker servers.

## 1) No More Ad-Hoc Remote Shell

Use `deploy/clusterctl.py` for sync/restart/health actions.

- Linux remote commands are sent as script stdin (`bash -s`), not inline quoted strings.
- Windows remote commands are sent as PowerShell `-EncodedCommand`, not escaped one-liners.
- Deploy flow becomes repeatable and idempotent:
  - sync source to Windows orchestrator + Linux worker host
  - restart daemons/tasks in a known order
  - verify health endpoints

### Commands

```bash
python deploy/clusterctl.py deploy
python deploy/clusterctl.py health
```

## 2) GitHub-Based Auto Sync

Both servers should run from a real git clone of the same repo branch.
Do not run production from manually copied folders.

### Linux auto-sync (systemd timer)

Files:
- `deploy/autosync/linux/supergod-sync.sh`
- `deploy/autosync/linux/supergod-sync.service`
- `deploy/autosync/linux/supergod-sync.timer`

Install example:

```bash
install -m 755 deploy/autosync/linux/supergod-sync.sh /usr/local/bin/supergod-sync.sh
cp deploy/autosync/linux/supergod-sync.service /etc/systemd/system/
cp deploy/autosync/linux/supergod-sync.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now supergod-sync.timer
```

The sync job:
- fetches `origin/main`
- hard-resets to latest commit when changed
- runs `pip install -e /opt/supergod`
- restarts orchestrator/worker units

### Windows auto-sync (Scheduled Task)

Files:
- `deploy/autosync/windows/supergod-sync.ps1`
- `deploy/autosync/windows/register-supergod-sync-task.ps1`

Register task:

```powershell
powershell -ExecutionPolicy Bypass -File C:\supergod\deploy\autosync\windows\register-supergod-sync-task.ps1
```

## 3) Required Hardening Upgrades

1. Move orchestrator host to Linux (recommended). This removes Windows task/shell edge cases.
2. Add a build fingerprint to worker registration (`version + git_sha`) and reject mismatches.
3. Add CI deploy on push to `main`:
   - Push code
   - Restart orchestrator/workers
   - Run health checks (`/healthz`, `/snapshot`, `/mission`)
4. Keep all production changes in git commits only (no hot edits on servers).

## 4) Enforcement Rule

Operational rule: if a deploy action is not represented by `clusterctl.py` or CI workflow, it is not allowed in production.

