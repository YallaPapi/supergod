#!/bin/bash
set -euo pipefail

# Supergod Worker Setup
# Run this ONCE on each worker Hetzner server
# Usage: ./setup_worker.sh <orchestrator-ip> <worker-name>

ORCHESTRATOR_IP="${1:?Usage: $0 <orchestrator-ip> <worker-name>}"
WORKER_NAME="${2:?Usage: $0 <orchestrator-ip> <worker-name>}"

echo "=== Supergod Worker Setup: $WORKER_NAME ==="
echo "Orchestrator: $ORCHESTRATOR_IP"

# Install system deps
apt-get update
apt-get install -y python3 python3-pip python3-venv git nodejs npm

# Install Codex CLI
npm install -g @openai/codex

# Login to Codex (interactive, one-time)
echo ""
echo "=== Log in to your Codex account ==="
echo "This will open a browser-based auth flow."
echo ""
codex login --device-auth

# Clone the shared repo
WORKSPACE="/workspace"
if [ ! -d "$WORKSPACE/.git" ]; then
    git clone "git@${ORCHESTRATOR_IP}:/srv/supergod-repo.git" "$WORKSPACE"
fi

# Configure git
cd "$WORKSPACE"
git config user.name "$WORKER_NAME"
git config user.email "${WORKER_NAME}@supergod.local"

# Install supergod
SUPERGOD_DIR="/opt/supergod"
if [ ! -d "$SUPERGOD_DIR" ]; then
    echo "Clone or copy the supergod repo to $SUPERGOD_DIR first"
    exit 1
fi

cd "$SUPERGOD_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Create systemd service
cat > /etc/systemd/system/supergod-worker.service << EOF
[Unit]
Description=Supergod Worker ($WORKER_NAME)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/supergod
ExecStart=/opt/supergod/.venv/bin/supergod-worker --name $WORKER_NAME --orchestrator ws://${ORCHESTRATOR_IP}:8080 --workdir /workspace
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable supergod-worker
systemctl start supergod-worker

echo ""
echo "=== Worker $WORKER_NAME setup complete ==="
echo "Connecting to ws://${ORCHESTRATOR_IP}:8080"
echo "Check status: systemctl status supergod-worker"
echo "View logs: journalctl -u supergod-worker -f"
