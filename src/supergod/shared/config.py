"""Shared configuration for supergod components."""

import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Orchestrator settings
ORCHESTRATOR_HOST = os.getenv("SUPERGOD_HOST", "0.0.0.0")
ORCHESTRATOR_PORT = int(os.getenv("SUPERGOD_PORT", "8080"))
ORCHESTRATOR_WS_URL = os.getenv(
    "SUPERGOD_WS_URL", f"ws://localhost:{ORCHESTRATOR_PORT}"
)
SUPERGOD_AUTH_TOKEN = os.getenv("SUPERGOD_AUTH_TOKEN", "")

# Worker settings
WORKER_NAME = os.getenv("SUPERGOD_WORKER_NAME", "worker-1")
WORKER_WORKDIR = os.getenv("SUPERGOD_WORKDIR", "/workspace")
WORKER_USE_WORKTREES = _env_bool("SUPERGOD_WORKER_USE_WORKTREES", True)
WORKER_WORKTREE_ROOT = os.getenv(
    "SUPERGOD_WORKER_WORKTREE_ROOT", ".supergod-worktrees"
)

# Codex settings
CODEX_BIN = os.getenv("SUPERGOD_CODEX_BIN", "codex")
CODEX_TIMEOUT = int(os.getenv("SUPERGOD_CODEX_TIMEOUT", "600"))  # 10 min default
CODEX_SANDBOX = os.getenv("SUPERGOD_CODEX_SANDBOX", "workspace-write")
CODEX_KILL_TIMEOUT = int(os.getenv("SUPERGOD_CODEX_KILL_TIMEOUT", "5"))  # seconds to wait after SIGTERM

# Brain settings
BRAIN_PARSE_RETRIES = int(os.getenv("SUPERGOD_BRAIN_PARSE_RETRIES", "1"))
PLANNING_INTERVAL = int(os.getenv("SUPERGOD_PLANNING_INTERVAL", "3"))

# Subtask retry settings
SUBTASK_MAX_RETRIES = int(os.getenv("SUPERGOD_SUBTASK_MAX_RETRIES", "2"))
ENABLE_STUCK_DETECTION = _env_bool("SUPERGOD_ENABLE_STUCK_DETECTION", True)
LEASE_SWEEP_INTERVAL = int(os.getenv("SUPERGOD_LEASE_SWEEP_INTERVAL", "30"))
SUBTASK_LEASE_TIMEOUT = int(os.getenv("SUPERGOD_SUBTASK_LEASE_TIMEOUT", "900"))
DISPATCH_INTERVAL = float(os.getenv("SUPERGOD_DISPATCH_INTERVAL", "1.0"))
MAX_WORKERS_PER_TASK = int(os.getenv("SUPERGOD_MAX_WORKERS_PER_TASK", "0"))

# Skill library settings
SKILLS_ENABLED = _env_bool("SUPERGOD_SKILLS_ENABLED", True)
SKILLS_PROFILE = os.getenv("SUPERGOD_SKILLS_PROFILE", "default")
SKILLS_INCLUDE_PROJECT_SPECIFIC = _env_bool(
    "SUPERGOD_SKILLS_INCLUDE_PROJECT_SPECIFIC", True
)
SKILLS_MAX_SKILLS = int(os.getenv("SUPERGOD_SKILLS_MAX_SKILLS", "6"))
SKILLS_MAX_CHARS = int(os.getenv("SUPERGOD_SKILLS_MAX_CHARS", "5000"))

# Database
DB_PATH = os.getenv("SUPERGOD_DB_PATH", "supergod.db")

# Reconnection
RECONNECT_DELAY_INITIAL = 5  # seconds
RECONNECT_DELAY_MAX = 60  # seconds
RECONNECT_JITTER_MAX = 3  # seconds — max random jitter added to backoff

# Ping/pong
PING_INTERVAL = 30  # seconds
PING_TIMEOUT = 10  # seconds
SERVER_PING_INTERVAL = 30  # seconds — how often server pings workers
SERVER_PING_TIMEOUT = 15  # seconds — how long to wait for pong before declaring dead
