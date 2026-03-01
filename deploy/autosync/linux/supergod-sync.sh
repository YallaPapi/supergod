#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/supergod}"
BRANCH="${BRANCH:-main}"
VENV_BIN="${VENV_BIN:-$REPO_DIR/.venv/bin}"

if [[ ! -d "$REPO_DIR/.git" ]]; then
  echo "supergod-sync: $REPO_DIR is not a git repo" >&2
  exit 1
fi

cd "$REPO_DIR"
git fetch origin "$BRANCH"

local_rev="$(git rev-parse HEAD)"
remote_rev="$(git rev-parse "origin/$BRANCH")"

if [[ "$local_rev" == "$remote_rev" ]]; then
  echo "supergod-sync: no updates ($local_rev)"
  exit 0
fi

echo "supergod-sync: updating $local_rev -> $remote_rev"
git reset --hard "origin/$BRANCH"
"$VENV_BIN/pip" install -e "$REPO_DIR"

if systemctl list-unit-files | grep -q '^supergod-orchestrator\.service'; then
  systemctl restart supergod-orchestrator
fi

mapfile -t worker_units < <(systemctl list-units --all --type=service --no-legend 'supergod-worker@*.service' | awk '{print $1}')
if [[ "${#worker_units[@]}" -gt 0 ]]; then
  systemctl restart "${worker_units[@]}"
elif [[ -x "$REPO_DIR/start_workers.sh" ]]; then
  pkill -f '[s]upergod.worker.daemon' || true
  "$REPO_DIR/start_workers.sh" >/tmp/supergod_workers_autosync.log 2>&1 || true
fi

echo "supergod-sync: update complete"
