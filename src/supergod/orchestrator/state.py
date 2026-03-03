"""SQLite state management for the orchestrator."""

import json
import logging
from datetime import datetime, timezone

import aiosqlite

from supergod.shared.config import DB_PATH
from supergod.shared.protocol import TaskStatus, WorkerStatus, new_id

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    prompt TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 100,
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
    attempt_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT DEFAULT '',
    failure_category TEXT DEFAULT '',
    execution_token TEXT DEFAULT '',
    lease_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workers (
    name TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'offline',
    current_subtask TEXT DEFAULT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    step TEXT NOT NULL,
    state_snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_events (
    event_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_subtasks_task_status
ON subtasks(task_id, status);

CREATE INDEX IF NOT EXISTS idx_checkpoints_task
ON checkpoints(task_id, created_at);

CREATE INDEX IF NOT EXISTS idx_task_events_task
ON task_events(task_id, created_at);
"""


class StateDB:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._ensure_schema_compat()
        await self._db.commit()
        log.info("Database initialized at %s", self.db_path)

    async def _column_exists(self, table: str, column: str) -> bool:
        async with self._db.execute(f"PRAGMA table_info({table})") as cur:
            rows = await cur.fetchall()
            return any(r["name"] == column for r in rows)

    async def _ensure_schema_compat(self) -> None:
        # Lightweight forward-only migration for older DB files.
        if not await self._column_exists("tasks", "priority"):
            await self._db.execute(
                "ALTER TABLE tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT 100"
            )
        if not await self._column_exists("subtasks", "attempt_count"):
            await self._db.execute(
                "ALTER TABLE subtasks ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0"
            )
        if not await self._column_exists("subtasks", "error_message"):
            await self._db.execute(
                "ALTER TABLE subtasks ADD COLUMN error_message TEXT DEFAULT ''"
            )
        if not await self._column_exists("subtasks", "failure_category"):
            await self._db.execute(
                "ALTER TABLE subtasks ADD COLUMN failure_category TEXT DEFAULT ''"
            )
        if not await self._column_exists("subtasks", "execution_token"):
            await self._db.execute(
                "ALTER TABLE subtasks ADD COLUMN execution_token TEXT DEFAULT ''"
            )
        if not await self._column_exists("subtasks", "lease_version"):
            await self._db.execute(
                "ALTER TABLE subtasks ADD COLUMN lease_version INTEGER NOT NULL DEFAULT 0"
            )

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # --- Tasks ---

    async def create_task(
        self, task_id: str, prompt: str, priority: int = 100
    ) -> None:
        now = self._now()
        await self._db.execute(
            "INSERT INTO tasks (task_id, prompt, status, priority, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, prompt, TaskStatus.PENDING, priority, now, now),
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
            "SELECT * FROM tasks ORDER BY priority ASC, created_at DESC"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_dispatchable_tasks(self) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM tasks WHERE status IN (?, ?) ORDER BY priority ASC, created_at ASC",
            (TaskStatus.ASSIGNED, TaskStatus.RUNNING),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_resumable_tasks(self) -> list[dict]:
        """Get non-terminal tasks for crash recovery."""
        async with self._db.execute(
            "SELECT * FROM tasks WHERE status NOT IN (?, ?, ?) ORDER BY created_at",
            (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED),
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

    async def touch_subtask(self, subtask_id: str) -> None:
        await self._db.execute(
            "UPDATE subtasks SET updated_at = ? WHERE subtask_id = ?",
            (self._now(), subtask_id),
        )
        await self._db.commit()

    async def claim_subtask_execution(
        self, subtask_id: str, worker_name: str, execution_token: str
    ) -> dict | None:
        """Atomically claim a pending subtask for execution."""
        now = self._now()
        cur = await self._db.execute(
            """UPDATE subtasks
               SET status = ?, worker_name = ?, execution_token = ?,
                   lease_version = lease_version + 1, updated_at = ?
               WHERE subtask_id = ? AND status = ?""",
            (
                TaskStatus.RUNNING,
                worker_name,
                execution_token,
                now,
                subtask_id,
                TaskStatus.PENDING,
            ),
        )
        await self._db.commit()
        if cur.rowcount == 0:
            return None
        return await self.get_subtask(subtask_id)

    async def release_subtask_execution(
        self,
        subtask_id: str,
        execution_token: str,
        next_status: TaskStatus = TaskStatus.PENDING,
    ) -> bool:
        """Release a claimed subtask only if token still matches."""
        cur = await self._db.execute(
            """UPDATE subtasks
               SET status = ?, worker_name = NULL, execution_token = '', updated_at = ?
               WHERE subtask_id = ? AND execution_token = ?""",
            (next_status, self._now(), subtask_id, execution_token),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def get_subtask(self, subtask_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM subtasks WHERE subtask_id = ?",
            (subtask_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_subtasks_for_task(self, task_id: str) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM subtasks WHERE task_id = ? ORDER BY created_at",
            (task_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_all_subtasks(self) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM subtasks ORDER BY created_at"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_orphan_subtasks(self) -> list[dict]:
        async with self._db.execute(
            """SELECT s.*
               FROM subtasks s
               LEFT JOIN tasks t ON t.task_id = s.task_id
               WHERE t.task_id IS NULL"""
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def delete_subtask(self, subtask_id: str) -> None:
        await self._db.execute(
            "DELETE FROM subtasks WHERE subtask_id = ?",
            (subtask_id,),
        )
        await self._db.commit()

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

    async def analyze_dependency_issues(self, task_id: str) -> dict:
        """Detect missing references and circular dependencies for a task DAG."""
        subtasks = await self.get_subtasks_for_task(task_id)
        ids = {s["subtask_id"] for s in subtasks}
        deps_by_id: dict[str, list[str]] = {}
        missing_dependencies: dict[str, list[str]] = {}

        for s in subtasks:
            sid = s["subtask_id"]
            deps = json.loads(s["depends_on"])
            deps_by_id[sid] = [d for d in deps if d in ids]
            missing = sorted({d for d in deps if d not in ids})
            if missing:
                missing_dependencies[sid] = missing

        cycle_nodes: set[str] = set()
        color: dict[str, int] = {sid: 0 for sid in ids}  # 0=unvisited,1=visiting,2=done
        path: list[str] = []
        path_index: dict[str, int] = {}

        def dfs(node: str) -> None:
            color[node] = 1
            path_index[node] = len(path)
            path.append(node)

            for dep in deps_by_id.get(node, []):
                dep_color = color.get(dep, 0)
                if dep_color == 0:
                    dfs(dep)
                elif dep_color == 1:
                    start = path_index.get(dep, 0)
                    cycle_nodes.update(path[start:])
                    cycle_nodes.add(dep)

            path.pop()
            path_index.pop(node, None)
            color[node] = 2

        for sid in ids:
            if color[sid] == 0:
                dfs(sid)

        return {
            "cycle_nodes": sorted(cycle_nodes),
            "missing_dependencies": missing_dependencies,
        }

    async def count_running_subtasks(self, task_id: str) -> int:
        async with self._db.execute(
            "SELECT COUNT(*) AS c FROM subtasks WHERE task_id = ? AND status = ?",
            (task_id, TaskStatus.RUNNING),
        ) as cur:
            row = await cur.fetchone()
            return int(row["c"]) if row else 0

    async def get_running_subtasks(self) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM subtasks WHERE status = ? ORDER BY updated_at",
            (TaskStatus.RUNNING,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_stale_running_subtasks(
        self, max_age_seconds: int
    ) -> list[dict]:
        now = datetime.now(timezone.utc)
        stale: list[dict] = []
        for subtask in await self.get_running_subtasks():
            updated_raw = subtask.get("updated_at")
            try:
                updated_at = datetime.fromisoformat(updated_raw)
            except Exception:
                stale.append(subtask)
                continue
            age = (now - updated_at).total_seconds()
            if age >= max_age_seconds:
                stale.append(subtask)
        return stale

    async def cascade_failure(
        self, task_id: str, failed_subtask_id: str
    ) -> list[str]:
        """Block dependents of a failed subtask and return blocked IDs."""
        subtasks = await self.get_subtasks_for_task(task_id)
        failed_ids = {failed_subtask_id}
        blocked: list[str] = []
        changed = True

        while changed:
            changed = False
            for s in subtasks:
                sid = s["subtask_id"]
                if sid in failed_ids:
                    continue
                if s["status"] in (
                    TaskStatus.BLOCKED,
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                ):
                    continue
                deps = json.loads(s["depends_on"])
                if any(d in failed_ids for d in deps):
                    await self.update_subtask(
                        sid,
                        status=TaskStatus.BLOCKED,
                        failure_category="dependency_failed",
                        error_message=f"Blocked by failed dependency ({failed_subtask_id})",
                    )
                    failed_ids.add(sid)
                    blocked.append(sid)
                    changed = True
        return blocked

    async def all_terminal(self, task_id: str) -> bool:
        subtasks = await self.get_subtasks_for_task(task_id)
        terminal = {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
        }
        return all(s["status"] in terminal for s in subtasks)

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

    async def get_worker(self, name: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM workers WHERE name = ?",
            (name,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def reset_worker_leases(self) -> None:
        """Clear stale worker task leases after orchestrator restart."""
        await self._db.execute(
            "UPDATE workers SET status = ?, current_subtask = NULL, last_seen = ?",
            (WorkerStatus.OFFLINE, self._now()),
        )
        await self._db.commit()

    # --- Checkpoints ---

    async def save_checkpoint(
        self, task_id: str, step: str, state_snapshot: dict
    ) -> str:
        checkpoint_id = new_id()
        now = self._now()
        await self._db.execute(
            "INSERT INTO checkpoints (checkpoint_id, task_id, step, state_snapshot, created_at) VALUES (?, ?, ?, ?, ?)",
            (checkpoint_id, task_id, step, json.dumps(state_snapshot), now),
        )
        await self._db.commit()
        return checkpoint_id

    async def get_latest_checkpoint(self, task_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM checkpoints WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            result = dict(row)
            try:
                result["state_snapshot"] = json.loads(result["state_snapshot"])
            except Exception:
                result["state_snapshot"] = {}
            return result

    async def add_task_event(
        self, task_id: str, event_type: str, payload: dict
    ) -> str:
        event_id = new_id()
        now = self._now()
        await self._db.execute(
            "INSERT INTO task_events (event_id, task_id, event_type, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (event_id, task_id, event_type, json.dumps(payload), now),
        )
        await self._db.commit()
        return event_id

    async def get_task_events(
        self, task_id: str, limit: int = 200
    ) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM task_events WHERE task_id = ? ORDER BY created_at DESC LIMIT ?",
            (task_id, limit),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        for row in rows:
            try:
                row["payload"] = json.loads(row.get("payload", "{}"))
            except Exception:
                row["payload"] = {}
        return rows
