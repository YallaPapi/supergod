"""SQLite state management for the orchestrator."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import aiosqlite

from supergod.shared.config import DB_PATH
from supergod.shared.protocol import TaskStatus, WorkerStatus

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    prompt TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    summary TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS subtasks (
    subtask_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    prompt TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    worker_name TEXT DEFAULT NULL,
    branch TEXT NOT NULL,
    commit_sha TEXT DEFAULT '',
    depends_on TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workers (
    name TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'offline',
    current_subtask TEXT DEFAULT NULL,
    last_seen TEXT NOT NULL
);
"""


class StateDB:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        log.info("Database initialized at %s", self.db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # --- Tasks ---

    async def create_task(self, task_id: str, prompt: str) -> None:
        now = self._now()
        await self._db.execute(
            "INSERT INTO tasks (task_id, prompt, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, prompt, TaskStatus.PENDING, now, now),
        )
        await self._db.commit()

    async def update_task_status(
        self, task_id: str, status: TaskStatus, summary: str = ""
    ) -> None:
        fields = {"status": status, "updated_at": self._now()}
        if summary:
            fields["summary"] = summary
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        await self._db.execute(
            f"UPDATE tasks SET {set_clause} WHERE task_id = ?",
            (*fields.values(), task_id),
        )
        await self._db.commit()

    async def get_task(self, task_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_all_tasks(self) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # --- Subtasks ---

    async def create_subtask(
        self,
        subtask_id: str,
        task_id: str,
        prompt: str,
        branch: str,
        depends_on: list[str] | None = None,
    ) -> None:
        now = self._now()
        await self._db.execute(
            "INSERT INTO subtasks (subtask_id, task_id, prompt, status, branch, depends_on, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                subtask_id,
                task_id,
                prompt,
                TaskStatus.PENDING,
                branch,
                json.dumps(depends_on or []),
                now,
                now,
            ),
        )
        await self._db.commit()

    async def update_subtask(self, subtask_id: str, **kwargs) -> None:
        kwargs["updated_at"] = self._now()
        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        await self._db.execute(
            f"UPDATE subtasks SET {set_clause} WHERE subtask_id = ?",
            (*kwargs.values(), subtask_id),
        )
        await self._db.commit()

    async def get_subtasks_for_task(self, task_id: str) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM subtasks WHERE task_id = ? ORDER BY created_at",
            (task_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_ready_subtasks(self, task_id: str) -> list[dict]:
        """Get subtasks that are pending and have all dependencies completed."""
        subtasks = await self.get_subtasks_for_task(task_id)
        completed = {
            s["subtask_id"]
            for s in subtasks
            if s["status"] == TaskStatus.COMPLETED
        }
        ready = []
        for s in subtasks:
            if s["status"] != TaskStatus.PENDING:
                continue
            deps = json.loads(s["depends_on"])
            if all(d in completed for d in deps):
                ready.append(s)
        return ready

    # --- Workers ---

    async def upsert_worker(self, name: str, status: WorkerStatus) -> None:
        now = self._now()
        await self._db.execute(
            """INSERT INTO workers (name, status, last_seen) VALUES (?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET status = ?, last_seen = ?""",
            (name, status, now, status, now),
        )
        await self._db.commit()

    async def set_worker_task(
        self, name: str, subtask_id: str | None
    ) -> None:
        status = WorkerStatus.BUSY if subtask_id else WorkerStatus.IDLE
        await self._db.execute(
            "UPDATE workers SET current_subtask = ?, status = ?, last_seen = ? WHERE name = ?",
            (subtask_id, status, self._now(), name),
        )
        await self._db.commit()

    async def get_idle_workers(self) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM workers WHERE status = ?", (WorkerStatus.IDLE,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_all_workers(self) -> list[dict]:
        async with self._db.execute("SELECT * FROM workers") as cur:
            return [dict(r) for r in await cur.fetchall()]
