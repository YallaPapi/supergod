"""Shared configuration for supergod components."""

from __future__ import annotations

import os


# Orchestrator settings
ORCHESTRATOR_HOST = os.getenv("SUPERGOD_HOST", "0.0.0.0")
ORCHESTRATOR_PORT = int(os.getenv("SUPERGOD_PORT", "8080"))
ORCHESTRATOR_WS_URL = os.getenv(
    "SUPERGOD_WS_URL", f"ws://localhost:{ORCHESTRATOR_PORT}"
)

# Worker settings
WORKER_NAME = os.getenv("SUPERGOD_WORKER_NAME", "worker-1")
WORKER_WORKDIR = os.getenv("SUPERGOD_WORKDIR", "/workspace")

# Codex settings
CODEX_BIN = os.getenv("SUPERGOD_CODEX_BIN", "codex")
CODEX_TIMEOUT = int(os.getenv("SUPERGOD_CODEX_TIMEOUT", "600"))  # 10 min default

# Database
DB_PATH = os.getenv("SUPERGOD_DB_PATH", "supergod.db")

# Reconnection
RECONNECT_DELAY_INITIAL = 5  # seconds
RECONNECT_DELAY_MAX = 60  # seconds

# Ping/pong
PING_INTERVAL = 30  # seconds
PING_TIMEOUT = 10  # seconds
