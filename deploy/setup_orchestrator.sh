#!/bin/bash
set -euo pipefail

# Supergod Orchestrator Setup
# Run this ONCE on the orchestrator Hetzner server

echo "=== Supergod Orchestrator Setup ==="

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

# Create workspace directory
WORKSPACE="/workspace"
mkdir -p "$WORKSPACE"

# Init a bare git repo for coordination
GIT_REPO="/srv/supergod-repo.git"
if [ ! -d "$GIT_REPO" ]; then
    git init --bare "$GIT_REPO"
    echo "Created bare git repo at $GIT_REPO"
fi

# Clone the repo for orchestrator's own use
if [ ! -d "$WORKSPACE/.git" ]; then
    git clone "$GIT_REPO" "$WORKSPACE"
    cd "$WORKSPACE"
    git checkout -b main
    echo "# Project" > README.md
    git add .
    git commit -m "initial commit"
    git push -u origin main
fi

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
cat > /etc/systemd/system/supergod-orchestrator.service << 'EOF'
[Unit]
Description=Supergod Orchestrator
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/supergod
ExecStart=/opt/supergod/.venv/bin/supergod-orchestrator --workdir /workspace --db /var/lib/supergod/supergod.db
Restart=always
RestartSec=5
Environment=SUPERGOD_HOST=0.0.0.0
Environment=SUPERGOD_PORT=8080

[Install]
WantedBy=multi-user.target
EOF

mkdir -p /var/lib/supergod

systemctl daemon-reload
systemctl enable supergod-orchestrator
systemctl start supergod-orchestrator

echo ""
echo "=== Orchestrator setup complete ==="
echo "Service running on port 8080"
echo "Check status: systemctl status supergod-orchestrator"
echo "View logs: journalctl -u supergod-orchestrator -f"
