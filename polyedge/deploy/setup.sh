#!/usr/bin/env bash
set -euo pipefail

# Install PostgreSQL if not present
if ! command -v psql &>/dev/null; then
    apt-get update && apt-get install -y postgresql postgresql-contrib
    systemctl enable postgresql && systemctl start postgresql
fi

# Create DB and user
sudo -u postgres psql -c "CREATE USER polyedge WITH PASSWORD 'polyedge';" 2>/dev/null || true
sudo -u postgres psql -c "CREATE DATABASE polyedge OWNER polyedge;" 2>/dev/null || true

# Clone/update repo
if [ ! -d /opt/polyedge ]; then
    echo "ERROR: Clone the repo to /opt/polyedge first"
    exit 1
fi
cd /opt/polyedge/polyedge

# Set up venv
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Run migrations
.venv/bin/alembic upgrade head

# Install systemd services
cp deploy/polyedge.service /etc/systemd/system/
cp deploy/polyedge-api.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable polyedge polyedge-api
systemctl restart polyedge polyedge-api

echo "PolyEdge deployed. API at http://$(hostname -I | awk '{print $1}'):8090"
