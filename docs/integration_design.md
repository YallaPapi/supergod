# Supergod v2: Framework Integration Design

**Date:** 2026-02-28
**Status:** Design proposal
**Scope:** Integrate the Top 15 features from multi-agent framework research into the existing supergod codebase.

---

## Current Architecture Summary

Before diving into changes, here is what we have today:

- **protocol.py**: Pydantic message types, serialize/deserialize, TaskStatus/WorkerStatus enums
- **state.py**: SQLite via aiosqlite. Three tables: tasks, subtasks, workers. Basic CRUD.
- **brain.py**: Codex-powered task decomposition (prompt -> JSON array of subtasks) and evaluation (test output -> pass/fail JSON)
- **scheduler.py**: In-memory dict of WorkerConnections, round-robin assignment (picks first idle worker), ping/pong health checks
- **server.py**: FastAPI WebSocket hub. Client WS + Worker WS. Task pipeline: decompose -> assign -> merge -> test -> evaluate
- **daemon.py**: Worker process. Connects to orchestrator, runs `codex exec`, streams JSONL events, commits to git branch
- **git_manager.py**: Sequential branch merging into main, pytest runner

Key limitations the research features address:
1. No crash recovery (if orchestrator dies, all state is lost except SQLite rows -- no resume logic)
2. No retry on subtask failure (fails once = done)
3. Round-robin worker assignment (no capability matching)
4. No validation gates (worker output accepted blindly)
5. No cascade failures (if subtask A fails, subtask B that depends on A still gets assigned)
6. No re-planning after partial completion
7. No human-in-the-loop checkpoints
8. Flat state model (no scoping)

---

## Implementation Phases

### Phase 1: Resilience Foundation (Features 1, 2, 11)

These three features make the system crash-safe and failure-aware. Everything else builds on them.

---

#### Feature 1: Checkpoint + Resume on Every State Transition
**Source:** LangGraph checkpoint with time-travel
**Priority:** MUST HAVE
**Dependencies:** None

**Problem:** If the orchestrator crashes mid-pipeline (e.g., during decomposition or after 3/5 subtasks complete), there is no way to resume. The `_process_task` coroutine in server.py is fire-and-forget.

**Design:**

Add a `checkpoints` table to SQLite that records every state transition with enough context to replay.

**Schema change in `state.py`:**

```sql
CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    step TEXT NOT NULL,          -- 'decomposed', 'subtask_assigned', 'subtask_completed',
                                 -- 'subtask_failed', 'merging', 'testing', 'evaluating'
    state_snapshot TEXT NOT NULL, -- JSON blob of full task state at this point
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_task ON checkpoints(task_id, created_at);
```

**New methods in `state.py` (class StateDB):**

```python
async def save_checkpoint(
    self, task_id: str, step: str, state_snapshot: dict
) -> str:
    """Save a checkpoint. Returns checkpoint_id."""
    cp_id = new_id()
    now = self._now()
    await self._db.execute(
        "INSERT INTO checkpoints (checkpoint_id, task_id, step, state_snapshot, created_at) VALUES (?, ?, ?, ?, ?)",
        (cp_id, task_id, step, json.dumps(state_snapshot), now),
    )
    await self._db.commit()
    return cp_id

async def get_latest_checkpoint(self, task_id: str) -> dict | None:
    """Get the most recent checkpoint for a task."""
    async with self._db.execute(
        "SELECT * FROM checkpoints WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
        (task_id,),
    ) as cur:
        row = await cur.fetchone()
        if row:
            result = dict(row)
            result["state_snapshot"] = json.loads(result["state_snapshot"])
            return result
        return None

async def get_resumable_tasks(self) -> list[dict]:
    """Get all tasks that were in progress (not terminal) when orchestrator last stopped."""
    async with self._db.execute(
        "SELECT * FROM tasks WHERE status NOT IN (?, ?, ?)",
        (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]
```

**Changes to `server.py`:**

Add a `_resume_in_progress_tasks()` function called from `startup()`:

```python
async def _resume_in_progress_tasks():
    """On orchestrator startup, resume any tasks that were in progress."""
    resumable = await db.get_resumable_tasks()
    for task in resumable:
        task_id = task["task_id"]
        checkpoint = await db.get_latest_checkpoint(task_id)
        if not checkpoint:
            log.warning("Task %s has no checkpoint, marking failed", task_id)
            await db.update_task_status(task_id, TaskStatus.FAILED, summary="Lost state on crash")
            continue

        step = checkpoint["step"]
        log.info("Resuming task %s from step '%s'", task_id, step)

        match step:
            case "decomposed" | "subtask_assigned" | "subtask_completed" | "subtask_failed":
                # Re-enter the assignment loop -- subtasks already exist in DB
                asyncio.create_task(_resume_assignment_loop(task_id))
            case "merging":
                asyncio.create_task(_resume_from_merge(task_id))
            case "testing":
                asyncio.create_task(_resume_from_test(task_id))
            case _:
                log.warning("Unknown checkpoint step '%s' for task %s", step, task_id)
```

Wrap every state transition in `_process_task` and `_check_task_progress` with checkpoint saves:

```python
# In _process_task, after decomposition:
await db.save_checkpoint(task_id, "decomposed", {
    "subtask_ids": [f"{task_id}-{st.id}" for st in subtasks],
})

# In _check_task_progress, after subtask completes:
await db.save_checkpoint(task_id, "subtask_completed", {
    "completed_subtask": subtask_id,
})

# Before merge:
await db.save_checkpoint(task_id, "merging", {"branches": branches})

# Before test:
await db.save_checkpoint(task_id, "testing", {})
```

**Files modified:** `state.py` (new table + 3 methods), `server.py` (checkpoint saves + startup resume)
**New files:** None
**Estimated effort:** Medium

---

#### Feature 2: Task DAG with Dependency Tracking + Cascade Failures
**Source:** Agno `Task` model with `_update_blocked_statuses()`
**Priority:** MUST HAVE
**Dependencies:** None (builds on existing `depends_on` column)

**Problem:** The current `get_ready_subtasks()` correctly checks that dependencies are completed before assigning. But if a dependency FAILS, its dependents sit in `pending` forever. The system waits until `all_subtasks_done()` which requires all subtasks to be completed or failed -- but blocked subtasks never transition.

**Design:**

Add a `BLOCKED` status and cascade failure logic.

**Change to `protocol.py`:**

```python
class TaskStatus(str, Enum):
    PENDING = "pending"
    DECOMPOSING = "decomposing"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"       # NEW: dependency failed
```

**New method in `state.py`:**

```python
async def cascade_failure(self, task_id: str, failed_subtask_id: str) -> list[str]:
    """Mark all subtasks that depend (transitively) on a failed subtask as BLOCKED.
    Returns list of blocked subtask IDs."""
    subtasks = await self.get_subtasks_for_task(task_id)
    failed_ids = {failed_subtask_id}
    blocked = []
    changed = True

    # BFS-style: keep propagating until no new blocks
    while changed:
        changed = False
        for s in subtasks:
            if s["subtask_id"] in failed_ids:
                continue
            if s["status"] in (TaskStatus.BLOCKED, TaskStatus.COMPLETED, TaskStatus.FAILED):
                continue
            deps = json.loads(s["depends_on"])
            if any(d in failed_ids for d in deps):
                await self.update_subtask(s["subtask_id"], status=TaskStatus.BLOCKED)
                failed_ids.add(s["subtask_id"])
                blocked.append(s["subtask_id"])
                changed = True

    return blocked

async def all_terminal(self, task_id: str) -> bool:
    """Check if all subtasks are in a terminal state (completed, failed, blocked, cancelled)."""
    terminal = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.CANCELLED}
    subtasks = await self.get_subtasks_for_task(task_id)
    return all(s["status"] in terminal for s in subtasks)
```

**Changes to `scheduler.py`:**

In `handle_task_error`, after marking the subtask as failed, call cascade:

```python
async def handle_task_error(
    self, worker_name: str, subtask_id: str, error: str
) -> list[str]:
    """Handle subtask failure. Returns list of cascade-blocked subtask IDs."""
    if worker_name in self.workers:
        self.workers[worker_name].status = WorkerStatus.IDLE
    await self.db.set_worker_task(worker_name, None)

    if "busy" in error.lower():
        await self.db.update_subtask(subtask_id, status=TaskStatus.PENDING)
        log.warning("Subtask %s returned to pending (worker %s was busy)", subtask_id, worker_name)
        return []
    else:
        await self.db.update_subtask(subtask_id, status=TaskStatus.FAILED)
        log.error("Subtask %s failed on %s: %s", subtask_id, worker_name, error)

        # Find parent task_id from subtask_id (convention: "{task_id}-{sub_id}")
        task_id = subtask_id.rsplit("-", 1)[0]
        blocked = await self.db.cascade_failure(task_id, subtask_id)
        if blocked:
            log.warning("Cascade blocked %d subtasks due to %s failure: %s",
                        len(blocked), subtask_id, blocked)
        return blocked
```

Replace `all_subtasks_done` with `all_terminal`:

```python
async def all_subtasks_done(self, task_id: str) -> bool:
    return await self.db.all_terminal(task_id)
```

**Files modified:** `protocol.py` (+1 enum value), `state.py` (+2 methods), `scheduler.py` (modify `handle_task_error`, `all_subtasks_done`)
**Estimated effort:** Small

---

#### Feature 11: State Context Manager with Auto-Revert
**Source:** OpenManus `state_context()`
**Priority:** MUST HAVE (for Phase 1 resilience)
**Dependencies:** Feature 1 (checkpoints)

**Problem:** If a state transition throws mid-way (e.g., DB write succeeds but WebSocket broadcast fails), state becomes inconsistent.

**Design:**

Add a context manager to `state.py` that wraps multi-step state transitions:

```python
from contextlib import asynccontextmanager

class StateDB:
    # ... existing methods ...

    @asynccontextmanager
    async def state_transition(self, entity_type: str, entity_id: str):
        """Context manager that auto-reverts state on exception.

        Usage:
            async with db.state_transition("subtask", subtask_id) as ctx:
                ctx.previous = await db.get_subtask(subtask_id)  # snapshot
                await db.update_subtask(subtask_id, status=TaskStatus.RUNNING)
                await some_risky_operation()
            # If exception in block: subtask reverted to previous state
        """
        class TransitionCtx:
            def __init__(self):
                self.previous: dict | None = None
                self.reverted = False

        ctx = TransitionCtx()
        try:
            yield ctx
        except Exception:
            if ctx.previous and not ctx.reverted:
                log.warning("Auto-reverting %s %s to previous state", entity_type, entity_id)
                if entity_type == "subtask":
                    await self.update_subtask(entity_id, **{
                        k: v for k, v in ctx.previous.items()
                        if k not in ("subtask_id", "created_at", "updated_at")
                    })
                elif entity_type == "task":
                    prev_status = ctx.previous.get("status", TaskStatus.PENDING)
                    await self.update_task_status(entity_id, prev_status)
                ctx.reverted = True
            raise
```

**Usage in `scheduler.py` (assign_subtask):**

```python
async def assign_subtask(self, subtask: dict, task_id: str) -> str | None:
    async with self._assignment_lock:
        idle = self.get_idle_workers()
        if not idle:
            return None
        worker = idle[0]
        subtask_id = subtask["subtask_id"]

        async with self.db.state_transition("subtask", subtask_id) as ctx:
            ctx.previous = subtask  # snapshot current state
            worker.status = WorkerStatus.BUSY
            await self.db.set_worker_task(worker.name, subtask_id)
            await self.db.update_subtask(
                subtask_id, status=TaskStatus.RUNNING, worker_name=worker.name,
            )
            msg = WorkerTaskMsg(id=subtask_id, prompt=subtask["prompt"], branch=subtask["branch"])
            await worker.ws.send_text(serialize(msg))  # if this throws, state reverts

        return worker.name
```

**Files modified:** `state.py` (+1 method), `scheduler.py` (wrap assign_subtask)
**Estimated effort:** Small

---

### Phase 2: Smart Scheduling (Features 3, 5, 8)

Once the system is crash-safe, make it smarter about HOW it assigns and executes work.

---

#### Feature 3: Dynamic Fan-Out/Fan-In with Typed Merge Reducers
**Source:** LangGraph Send API + Haystack merge handlers
**Priority:** MUST HAVE
**Dependencies:** Feature 2 (DAG)

**Problem:** The current decomposition produces a fixed list of subtasks. There is no mechanism for runtime fan-out (e.g., "apply this linting fix to all 15 files") or typed merging of concurrent results.

**Design:**

Two parts: (A) dynamic subtask creation at runtime, (B) typed merge reducers.

**(A) Dynamic fan-out -- new method in server.py:**

```python
async def _fan_out(task_id: str, template_prompt: str, items: list[dict]) -> list[str]:
    """Create N subtasks from a template at runtime. Returns subtask IDs.

    Args:
        task_id: Parent task
        template_prompt: Prompt with {item} placeholder
        items: List of dicts, each substituted into template
    """
    subtask_ids = []
    for item in items:
        subtask_id = f"{task_id}-{new_id()}"
        prompt = template_prompt.format(**item)
        branch = f"task/{subtask_id}"
        await db.create_subtask(
            subtask_id=subtask_id,
            task_id=task_id,
            prompt=prompt,
            branch=branch,
            depends_on=[],
        )
        subtask_ids.append(subtask_id)

    await db.save_checkpoint(task_id, "fan_out", {
        "new_subtask_ids": subtask_ids,
        "template": template_prompt,
    })
    assigned = await scheduler.try_assign_ready_subtasks(task_id)
    log.info("Fan-out created %d subtasks, assigned %d", len(subtask_ids), assigned)
    return subtask_ids
```

The brain's decomposition prompt can signal fan-out by returning a special subtask type:

```python
# In brain.py, extended Subtask dataclass:
@dataclass
class Subtask:
    id: str
    description: str
    depends_on: list[str]
    fan_out: dict | None = None  # {"template": "...", "items_from": "glob:src/**/*.py"}
```

**(B) Merge reducers -- new module `src/supergod/orchestrator/reducers.py`:**

```python
"""Typed merge reducers for combining concurrent worker outputs."""

from typing import Any, Callable

# Registry of reducer functions
_REDUCERS: dict[str, Callable[[Any, Any], Any]] = {}


def register_reducer(name: str, fn: Callable[[Any, Any], Any]):
    _REDUCERS[name] = fn


def get_reducer(name: str) -> Callable[[Any, Any], Any]:
    return _REDUCERS.get(name, _replace)


def _replace(old: Any, new: Any) -> Any:
    """Default: last writer wins."""
    return new


def _append_list(old: Any, new: Any) -> Any:
    """Append new items to existing list."""
    if not isinstance(old, list):
        old = [old] if old else []
    if not isinstance(new, list):
        new = [new]
    return old + new


def _merge_dict(old: Any, new: Any) -> Any:
    """Shallow merge dicts, new keys overwrite old."""
    if not isinstance(old, dict):
        return new
    if not isinstance(new, dict):
        return new
    result = {**old, **new}
    return result


def _union_set(old: Any, new: Any) -> Any:
    """Union of sets/lists, deduplicated."""
    old_set = set(old) if isinstance(old, (list, set)) else set()
    new_set = set(new) if isinstance(new, (list, set)) else set()
    return list(old_set | new_set)


# Register built-in reducers
register_reducer("replace", _replace)
register_reducer("append", _append_list)
register_reducer("merge", _merge_dict)
register_reducer("union", _union_set)
```

Add a `reducer` column to subtasks table to specify how results merge:

```sql
-- In state.py SCHEMA, add to subtasks table:
reducer TEXT DEFAULT 'replace'
```

**When fan-in completes** (all subtasks of a fan-out done), the orchestrator applies reducers to combine results before passing to the next phase.

**Files modified:** `state.py` (schema + new column), `brain.py` (extended Subtask), `server.py` (fan_out function)
**New files:** `src/supergod/orchestrator/reducers.py`
**Estimated effort:** Medium

---

#### Feature 5: LLM-as-Router for Worker Selection
**Source:** AutoGen SelectorGroupChat
**Priority:** HIGH VALUE
**Dependencies:** None

**Problem:** Current assignment picks `idle[0]` -- literally the first idle worker. No consideration of worker capabilities, load history, or task requirements.

**Design:**

Two-stage selection: rule-based pre-filter, then LLM picks from candidates.

**Step 1: Worker capabilities in protocol.py:**

```python
class WorkerReadyMsg(BaseModel):
    type: str = "ready"
    name: str
    capabilities: list[str] = []  # NEW: ["python", "typescript", "frontend", "devops"]
    max_concurrent: int = 1       # NEW: how many tasks this worker can handle
```

**Step 2: Store capabilities in state.py:**

```sql
-- Extend workers table:
ALTER TABLE workers ADD COLUMN capabilities TEXT DEFAULT '[]';
ALTER TABLE workers ADD COLUMN total_completed INTEGER DEFAULT 0;
ALTER TABLE workers ADD COLUMN total_failed INTEGER DEFAULT 0;
```

```python
async def upsert_worker(self, name: str, status: WorkerStatus,
                         capabilities: list[str] | None = None) -> None:
    now = self._now()
    caps = json.dumps(capabilities or [])
    await self._db.execute(
        """INSERT INTO workers (name, status, capabilities, last_seen) VALUES (?, ?, ?, ?)
           ON CONFLICT(name) DO UPDATE SET status = ?, capabilities = COALESCE(?, capabilities), last_seen = ?""",
        (name, status, caps, now, status, caps if capabilities else None, now),
    )
    await self._db.commit()
```

**Step 3: Two-stage selection in scheduler.py:**

```python
@dataclass
class WorkerConnection:
    name: str
    ws: WebSocket
    status: WorkerStatus = WorkerStatus.IDLE
    last_pong: float = 0.0
    capabilities: list[str] = field(default_factory=list)  # NEW

def _prefilter_workers(
    self, idle_workers: list[WorkerConnection], subtask: dict
) -> list[WorkerConnection]:
    """Rule-based pre-filter: capability match, load balance."""
    # For now, all idle workers are candidates
    # Future: parse subtask prompt for required capabilities
    return idle_workers

async def _llm_select_worker(
    self, candidates: list[WorkerConnection], subtask: dict
) -> WorkerConnection | None:
    """Use brain's Codex to pick the best worker from candidates.
    Falls back to first candidate if LLM selection fails."""
    if len(candidates) <= 1:
        return candidates[0] if candidates else None

    # Build selection prompt
    worker_descriptions = "\n".join(
        f"- {w.name}: capabilities={w.capabilities}"
        for w in candidates
    )
    prompt = (
        f"Pick the best worker for this task. Output ONLY the worker name, nothing else.\n\n"
        f"Task: {subtask['prompt'][:500]}\n\n"
        f"Available workers:\n{worker_descriptions}"
    )

    try:
        result = await run_codex_collect(prompt=prompt, workdir=".")
        chosen_name = result.final_message.strip()
        for w in candidates:
            if w.name == chosen_name:
                return w
    except Exception as e:
        log.warning("LLM worker selection failed: %s, using first candidate", e)

    return candidates[0]  # fallback
```

Update `assign_subtask` to use the two-stage selection:

```python
async def assign_subtask(self, subtask: dict, task_id: str) -> str | None:
    async with self._assignment_lock:
        idle = self.get_idle_workers()
        candidates = self._prefilter_workers(idle, subtask)
        if not candidates:
            return None

        worker = await self._llm_select_worker(candidates, subtask)
        if not worker:
            return None
        # ... rest of assignment logic unchanged
```

**Config gate:** Add `ENABLE_LLM_ROUTING = os.getenv("SUPERGOD_LLM_ROUTING", "false").lower() == "true"` to config.py. When disabled, falls back to `candidates[0]` immediately. LLM routing costs a Codex call per assignment, so it should be opt-in.

**Files modified:** `protocol.py` (WorkerReadyMsg fields), `state.py` (workers table + upsert_worker), `scheduler.py` (prefilter + llm_select + assign_subtask), `config.py` (+1 flag), `server.py` (pass capabilities on register)
**Estimated effort:** Medium

---

#### Feature 8: Context Variables Hidden from Model
**Source:** Swarm context variables
**Priority:** HIGH VALUE
**Dependencies:** None

**Problem:** Internal metadata (worker IDs, branch names, git SHAs, retry counts) is either lost between steps or pollutes the Codex prompt.

**Design:**

Add a `context_vars` dict that travels with tasks/subtasks but is NEVER included in prompts sent to Codex.

**Protocol change:**

```python
class WorkerTaskMsg(BaseModel):
    type: str = "task"
    id: str
    prompt: str
    branch: str
    workdir: str = "/workspace"
    context_vars: dict[str, Any] = {}  # NEW: hidden from Codex, visible to daemon logic
```

**Schema change in state.py:**

```sql
-- Add to subtasks table:
context_vars TEXT DEFAULT '{}'
```

```python
async def create_subtask(
    self, subtask_id: str, task_id: str, prompt: str, branch: str,
    depends_on: list[str] | None = None,
    context_vars: dict | None = None,
) -> None:
    now = self._now()
    await self._db.execute(
        "INSERT INTO subtasks (..., context_vars, ...) VALUES (..., ?, ...)",
        (..., json.dumps(context_vars or {}), ...),
    )
```

**Usage in daemon.py:**

```python
async def _execute_task(self, ws, msg) -> None:
    task_id = msg.id
    branch = msg.branch
    ctx = msg.context_vars  # available for daemon logic, NOT sent to codex

    retry_count = ctx.get("retry_count", 0)
    parent_commit = ctx.get("parent_commit", "")
    required_files = ctx.get("required_files", [])

    # Use context for git operations
    if parent_commit:
        await git_ops.checkout(self.workdir, parent_commit)

    # Pass only the prompt to codex -- context_vars stay hidden
    async for event in run_codex(prompt=msg.prompt, workdir=self.workdir):
        ...
```

**Files modified:** `protocol.py` (WorkerTaskMsg), `state.py` (schema + create_subtask), `daemon.py` (read context_vars), `scheduler.py` (pass context_vars in assign_subtask)
**Estimated effort:** Small

---

### Phase 3: Quality Gates (Features 4, 9, 12)

Now that scheduling is smart, ensure outputs are validated before acceptance.

---

#### Feature 4: Typed State with Merge Reducers
**Source:** LangGraph + Haystack
**Priority:** MUST HAVE
**Dependencies:** Feature 3 (reducers module already created)

**Problem:** When multiple workers complete concurrently, their results (git branches, test outputs, metadata) merge with no defined strategy. The current approach is "merge git branches sequentially and hope for no conflicts."

**Design:**

Define a `TaskState` Pydantic model with per-field reducers that the orchestrator maintains:

```python
# New: src/supergod/orchestrator/task_state.py

from pydantic import BaseModel, Field
from typing import Any
from supergod.orchestrator.reducers import get_reducer


class TaskState(BaseModel):
    """Typed shared state for a task. Each field has a merge reducer."""

    # Overwrite fields (last writer wins)
    current_phase: str = "pending"
    final_summary: str = ""

    # Append fields (accumulated across subtasks)
    completed_files: list[str] = Field(default_factory=list)
    error_log: list[str] = Field(default_factory=list)
    commit_shas: list[str] = Field(default_factory=list)

    # Custom merge fields
    test_results: dict[str, bool] = Field(default_factory=dict)  # file -> pass/fail

    class Config:
        # Define which reducer each field uses
        reducers = {
            "current_phase": "replace",
            "final_summary": "replace",
            "completed_files": "append",
            "error_log": "append",
            "commit_shas": "append",
            "test_results": "merge",
        }

    def apply_update(self, updates: dict[str, Any]) -> "TaskState":
        """Apply updates using per-field reducers."""
        data = self.model_dump()
        reducers = self.Config.reducers
        for key, value in updates.items():
            if key in data:
                reducer_name = reducers.get(key, "replace")
                reducer_fn = get_reducer(reducer_name)
                data[key] = reducer_fn(data[key], value)
        return TaskState(**data)
```

**Store in SQLite -- add to tasks table:**

```sql
ALTER TABLE tasks ADD COLUMN state_json TEXT DEFAULT '{}';
```

**Usage in server.py when subtask completes:**

```python
async def _on_subtask_complete(task_id: str, subtask_id: str, commit: str):
    task = await db.get_task(task_id)
    current_state = TaskState(**json.loads(task["state_json"]))

    updated = current_state.apply_update({
        "commit_shas": [commit],
        "completed_files": [subtask_id],  # or actual file list from worker
    })

    await db.execute(
        "UPDATE tasks SET state_json = ? WHERE task_id = ?",
        (updated.model_dump_json(), task_id),
    )
```

**Files modified:** `state.py` (schema), `server.py` (apply_update on completion)
**New files:** `src/supergod/orchestrator/task_state.py`
**Estimated effort:** Medium

---

#### Feature 9: Validation Gates (final_answer_checks)
**Source:** smolagents `final_answer_checks`
**Priority:** HIGH VALUE
**Dependencies:** None

**Problem:** Worker output is accepted as "completed" purely based on the worker reporting success. No validation that the diff is non-empty, touches the right files, or passes basic checks.

**Design:**

Add a validation pipeline that runs on the orchestrator BEFORE accepting a subtask as completed.

**New module `src/supergod/orchestrator/validation.py`:**

```python
"""Validation gates for worker output."""

import logging
from dataclasses import dataclass
from typing import Callable, Awaitable

log = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    passed: bool
    gate_name: str
    message: str


# Type alias for a validation gate function
GateFn = Callable[[str, str, dict], Awaitable[ValidationResult]]
# Args: workdir, branch, subtask_dict


async def check_diff_nonempty(workdir: str, branch: str, subtask: dict) -> ValidationResult:
    """Verify the worker actually changed something."""
    from supergod.orchestrator.git_manager import _git
    code, out, err = await _git(workdir, "diff", "--stat", f"main...{branch}")
    if not out.strip():
        return ValidationResult(False, "diff_nonempty", "Branch has no changes vs main")
    return ValidationResult(True, "diff_nonempty", f"Branch has changes: {out[:200]}")


async def check_no_syntax_errors(workdir: str, branch: str, subtask: dict) -> ValidationResult:
    """Run basic syntax check on changed files."""
    from supergod.orchestrator.git_manager import _git, _run
    code, out, err = await _git(workdir, "diff", "--name-only", f"main...{branch}")
    if not out:
        return ValidationResult(True, "syntax", "No files to check")

    files = out.strip().split("\n")
    py_files = [f for f in files if f.endswith(".py")]

    for py_file in py_files:
        rc, _, serr = await _run(["python", "-m", "py_compile", py_file], workdir)
        if rc != 0:
            return ValidationResult(False, "syntax", f"Syntax error in {py_file}: {serr}")

    return ValidationResult(True, "syntax", f"All {len(py_files)} Python files pass syntax check")


async def check_no_secrets(workdir: str, branch: str, subtask: dict) -> ValidationResult:
    """Scan diff for potential secrets."""
    from supergod.orchestrator.git_manager import _git
    code, out, err = await _git(workdir, "diff", f"main...{branch}")
    danger_patterns = ["API_KEY=", "SECRET=", "PASSWORD=", "-----BEGIN", "sk-", "xai-"]
    for pattern in danger_patterns:
        if pattern in out:
            return ValidationResult(False, "secrets", f"Potential secret found: {pattern}")
    return ValidationResult(True, "secrets", "No secrets detected")


# Default gate pipeline
DEFAULT_GATES: list[GateFn] = [
    check_diff_nonempty,
    check_no_syntax_errors,
    check_no_secrets,
]


async def run_validation_gates(
    workdir: str, branch: str, subtask: dict,
    gates: list[GateFn] | None = None,
) -> tuple[bool, list[ValidationResult]]:
    """Run all validation gates. Returns (all_passed, results)."""
    gates = gates or DEFAULT_GATES
    results = []
    for gate in gates:
        try:
            result = await gate(workdir, branch, subtask)
        except Exception as e:
            result = ValidationResult(False, gate.__name__, f"Gate crashed: {e}")
        results.append(result)
        if not result.passed:
            log.warning("Validation gate '%s' FAILED: %s", result.gate_name, result.message)

    all_passed = all(r.passed for r in results)
    return all_passed, results
```

**Integration in server.py -- modify subtask completion handler:**

```python
# In worker_ws handler, case "worker_task_complete":
async def _validate_and_accept(worker_name: str, subtask_id: str, commit: str):
    # Find the subtask
    subtask = ...  # query from DB

    # Run validation gates (only if remote exists for branch comparison)
    if has_remote_configured:
        passed, results = await run_validation_gates(workdir, subtask["branch"], subtask)
        if not passed:
            failed_gates = [r for r in results if not r.passed]
            error_msg = "; ".join(f"{r.gate_name}: {r.message}" for r in failed_gates)
            # Treat as failure, trigger retry or cascade
            await scheduler.handle_task_error(worker_name, subtask_id, f"Validation failed: {error_msg}")
            return

    # Gates passed -- accept the result
    await scheduler.handle_task_complete(worker_name, subtask_id, commit)
```

**Files modified:** `server.py` (validation before acceptance)
**New files:** `src/supergod/orchestrator/validation.py`
**Estimated effort:** Medium

---

#### Feature 12: Error Traces Fed Back for Self-Correction
**Source:** smolagents error-in-memory
**Priority:** HIGH VALUE
**Dependencies:** Feature 8 (context_vars)

**Problem:** When a subtask fails and gets retried, the retry gets the same prompt. The worker has no knowledge of what went wrong before.

**Design:**

On failure, capture the full error trace and inject it into the retry prompt.

**New method in `scheduler.py`:**

```python
async def retry_subtask_with_error(
    self, subtask_id: str, task_id: str, error: str, max_retries: int = 2
) -> str | None:
    """Retry a failed subtask with the error trace injected into the prompt."""
    subtask = await self.db.get_subtask(subtask_id)  # need to add this method to StateDB
    if not subtask:
        return None

    context_vars = json.loads(subtask.get("context_vars", "{}"))
    retry_count = context_vars.get("retry_count", 0)

    if retry_count >= max_retries:
        log.warning("Subtask %s exceeded max retries (%d)", subtask_id, max_retries)
        return None

    # Build enhanced prompt with error context
    error_history = context_vars.get("error_history", [])
    error_history.append(error)

    enhanced_prompt = (
        f"{subtask['prompt']}\n\n"
        f"IMPORTANT: Previous attempt(s) failed. Here are the errors:\n"
    )
    for i, err in enumerate(error_history, 1):
        enhanced_prompt += f"\n--- Attempt {i} Error ---\n{err[:1000]}\n"
    enhanced_prompt += "\nFix the issues from previous attempts. Do NOT repeat the same mistakes."

    # Create a new subtask for the retry (don't reuse the failed one)
    new_subtask_id = f"{subtask_id}-retry{retry_count + 1}"
    new_context = {
        **context_vars,
        "retry_count": retry_count + 1,
        "error_history": error_history,
        "original_subtask_id": subtask_id,
    }

    await self.db.create_subtask(
        subtask_id=new_subtask_id,
        task_id=task_id,
        prompt=enhanced_prompt,
        branch=subtask["branch"],  # same branch
        depends_on=[],
        context_vars=new_context,
    )

    return await self.assign_subtask(
        {"subtask_id": new_subtask_id, "prompt": enhanced_prompt, "branch": subtask["branch"]},
        task_id,
    )
```

**Integration in server.py `_check_task_progress`:**

When a subtask fails, attempt retry before cascade:

```python
# In _check_task_progress, when a subtask has failed:
subtask = await db.get_subtask(subtask_id)
if subtask and subtask["status"] == TaskStatus.FAILED:
    retry_worker = await scheduler.retry_subtask_with_error(
        subtask_id, task_id, error="..."  # from the error message
    )
    if retry_worker:
        log.info("Retrying subtask %s on %s with error context", subtask_id, retry_worker)
        return  # Don't cascade yet, wait for retry result
```

**Files modified:** `scheduler.py` (+retry method), `server.py` (retry before cascade), `state.py` (+get_subtask method)
**Estimated effort:** Medium

---

### Phase 4: Intelligence (Features 6, 7, 10)

Make the orchestrator brain smarter about planning and monitoring.

---

#### Feature 6: Orchestration Patterns as Swappable Strategies
**Source:** Semantic Kernel five patterns
**Priority:** HIGH VALUE
**Dependencies:** Features 2, 3

**Problem:** The current orchestrator has one hardcoded strategy: decompose -> fan-out to all idle workers -> merge. But different tasks need different patterns (some should be sequential, some need broadcast for consensus, some need a pipeline).

**Design:**

Define an abstract `OrchestrationStrategy` and implement three concrete strategies.

**New file `src/supergod/orchestrator/strategies.py`:**

```python
"""Orchestration strategies -- swappable patterns for task execution."""

import abc
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supergod.orchestrator.scheduler import Scheduler
    from supergod.orchestrator.state import StateDB
    from supergod.orchestrator.brain import Subtask

log = logging.getLogger(__name__)


class OrchestrationStrategy(abc.ABC):
    """Base class for orchestration patterns."""

    name: str = "base"

    @abc.abstractmethod
    async def execute(
        self,
        task_id: str,
        subtasks: list["Subtask"],
        scheduler: "Scheduler",
        db: "StateDB",
    ) -> None:
        """Execute the strategy. Creates subtasks in DB and starts assignment."""
        ...


class ConcurrentDispatch(OrchestrationStrategy):
    """Fan-out: assign all independent subtasks in parallel. Default strategy."""

    name = "concurrent"

    async def execute(self, task_id, subtasks, scheduler, db):
        from supergod.shared.protocol import new_id

        for st in subtasks:
            subtask_id = f"{task_id}-{st.id}"
            branch = f"task/{subtask_id}"
            await db.create_subtask(
                subtask_id=subtask_id,
                task_id=task_id,
                prompt=st.description,
                branch=branch,
                depends_on=[f"{task_id}-{d}" for d in st.depends_on],
            )

        assigned = await scheduler.try_assign_ready_subtasks(task_id)
        log.info("[%s] Created %d subtasks, assigned %d", self.name, len(subtasks), assigned)


class SequentialPipeline(OrchestrationStrategy):
    """Execute subtasks one at a time in order. Each depends on the previous."""

    name = "sequential"

    async def execute(self, task_id, subtasks, scheduler, db):
        prev_id = None
        for st in subtasks:
            subtask_id = f"{task_id}-{st.id}"
            branch = f"task/{subtask_id}"
            deps = [prev_id] if prev_id else []
            await db.create_subtask(
                subtask_id=subtask_id,
                task_id=task_id,
                prompt=st.description,
                branch=branch,
                depends_on=deps,
            )
            prev_id = subtask_id

        assigned = await scheduler.try_assign_ready_subtasks(task_id)
        log.info("[%s] Created %d sequential subtasks, assigned %d", self.name, len(subtasks), assigned)


class BroadcastConsensus(OrchestrationStrategy):
    """Send the same task to N workers, pick the best result (or majority vote)."""

    name = "broadcast"

    async def execute(self, task_id, subtasks, scheduler, db):
        from supergod.shared.protocol import new_id

        # For broadcast, we only use the first subtask and replicate it
        if not subtasks:
            return
        base_task = subtasks[0]

        idle_workers = scheduler.get_idle_workers()
        n_copies = min(len(idle_workers), 3)  # broadcast to up to 3 workers

        for i in range(n_copies):
            subtask_id = f"{task_id}-broadcast-{i}"
            branch = f"task/{subtask_id}"
            await db.create_subtask(
                subtask_id=subtask_id,
                task_id=task_id,
                prompt=base_task.description,
                branch=branch,
                depends_on=[],
            )

        assigned = await scheduler.try_assign_ready_subtasks(task_id)
        log.info("[%s] Broadcast to %d workers, assigned %d", self.name, n_copies, assigned)


# Strategy registry
STRATEGIES: dict[str, type[OrchestrationStrategy]] = {
    "concurrent": ConcurrentDispatch,
    "sequential": SequentialPipeline,
    "broadcast": BroadcastConsensus,
}


def get_strategy(name: str) -> OrchestrationStrategy:
    cls = STRATEGIES.get(name, ConcurrentDispatch)
    return cls()
```

**Strategy selection in brain.py:**

Extend the decomposition prompt to also output a recommended strategy:

```python
DECOMPOSE_PROMPT = """Output ONLY a raw JSON object with "strategy" and "subtasks" fields.

strategy: one of "concurrent", "sequential", "broadcast"
- Use "concurrent" when subtasks are independent (different files/modules)
- Use "sequential" when each step depends on the previous
- Use "broadcast" when you want multiple workers to attempt the same task

subtasks: array of objects with id, description, depends_on fields.

Task: {prompt}"""
```

**Integration in server.py `_process_task`:**

```python
async def _process_task(task_id: str, prompt: str):
    subtasks, strategy_name = await decompose_task(prompt, workdir)
    strategy = get_strategy(strategy_name)
    await db.update_task_status(task_id, TaskStatus.ASSIGNED)
    await strategy.execute(task_id, subtasks, scheduler, db)
```

**Files modified:** `brain.py` (decompose returns strategy), `server.py` (use strategy)
**New files:** `src/supergod/orchestrator/strategies.py`
**Estimated effort:** Medium

---

#### Feature 7: Planning Interval / Periodic Re-Evaluation
**Source:** smolagents planning_interval
**Priority:** HIGH VALUE
**Dependencies:** Feature 1 (checkpoints to know what has completed)

**Problem:** Once the brain decomposes a task, it never looks at the plan again. If early subtasks reveal that the approach is wrong, the remaining subtasks execute anyway.

**Design:**

After every N completed subtasks (configurable), pause assignment and ask the brain to re-evaluate.

**New config in config.py:**

```python
PLANNING_INTERVAL = int(os.getenv("SUPERGOD_PLANNING_INTERVAL", "3"))  # re-evaluate every N completions
```

**New method in brain.py:**

```python
REPLAN_PROMPT = """You are monitoring a multi-agent software development task.

Original goal: {original_prompt}

Completed subtasks so far:
{completed_summary}

Remaining subtasks:
{remaining_summary}

Are the remaining subtasks still correct and necessary? Consider:
1. Did completed work reveal the approach needs changing?
2. Are any remaining subtasks now unnecessary?
3. Are new subtasks needed that weren't in the original plan?

Output ONLY a JSON object:
{{
  "action": "continue" | "cancel_remaining" | "add_subtasks" | "replace_remaining",
  "reason": "brief explanation",
  "cancel_ids": [],
  "new_subtasks": []
}}"""


async def replan_check(
    original_prompt: str,
    completed_subtasks: list[dict],
    remaining_subtasks: list[dict],
    workdir: str = ".",
) -> dict:
    """Ask the brain if the plan still makes sense."""
    completed_summary = "\n".join(
        f"- [{s['subtask_id']}] {s['prompt'][:100]} -> {s['status']}"
        for s in completed_subtasks
    )
    remaining_summary = "\n".join(
        f"- [{s['subtask_id']}] {s['prompt'][:100]} (status: {s['status']})"
        for s in remaining_subtasks
    )

    full_prompt = REPLAN_PROMPT.format(
        original_prompt=original_prompt,
        completed_summary=completed_summary,
        remaining_summary=remaining_summary,
    )

    result = await run_codex_collect(prompt=full_prompt, workdir=workdir)
    try:
        return _extract_json_object(result.final_message)
    except Exception:
        return {"action": "continue", "reason": "Failed to parse replan output"}
```

**Integration in server.py:**

Track completion count per task and trigger re-evaluation:

```python
# Module-level counter
_completion_counts: dict[str, int] = {}

async def _check_task_progress(subtask_id: str):
    # ... existing logic to find task_id ...

    # Increment completion counter
    _completion_counts[task_id] = _completion_counts.get(task_id, 0) + 1

    if _completion_counts[task_id] % PLANNING_INTERVAL == 0:
        log.info("Planning interval reached for %s, re-evaluating", task_id)
        all_subtasks = await db.get_subtasks_for_task(task_id)
        completed = [s for s in all_subtasks if s["status"] == TaskStatus.COMPLETED]
        remaining = [s for s in all_subtasks if s["status"] in (TaskStatus.PENDING, TaskStatus.RUNNING)]

        if remaining:
            task = await db.get_task(task_id)
            plan = await replan_check(task["prompt"], completed, remaining, workdir)
            await _apply_replan(task_id, plan)

    # ... rest of existing logic (assign more, check if done) ...


async def _apply_replan(task_id: str, plan: dict):
    action = plan.get("action", "continue")
    match action:
        case "continue":
            log.info("Replan: continuing as planned. Reason: %s", plan.get("reason"))
        case "cancel_remaining":
            remaining = await db.get_subtasks_for_task(task_id)
            for s in remaining:
                if s["status"] == TaskStatus.PENDING:
                    await db.update_subtask(s["subtask_id"], status=TaskStatus.CANCELLED)
            log.info("Replan: cancelled remaining subtasks. Reason: %s", plan.get("reason"))
        case "add_subtasks":
            for new_st in plan.get("new_subtasks", []):
                subtask_id = f"{task_id}-{new_id()}"
                await db.create_subtask(
                    subtask_id=subtask_id,
                    task_id=task_id,
                    prompt=new_st.get("description", ""),
                    branch=f"task/{subtask_id}",
                    depends_on=new_st.get("depends_on", []),
                )
            await scheduler.try_assign_ready_subtasks(task_id)
```

**Files modified:** `brain.py` (+replan_check), `server.py` (+counter, +replan logic), `config.py` (+PLANNING_INTERVAL)
**Estimated effort:** Medium

---

#### Feature 10: Stuck Detection
**Source:** OpenManus duplicate message detection
**Priority:** NICE TO HAVE
**Dependencies:** None

**Problem:** Codex can loop infinitely, producing the same output over and over. The worker keeps streaming events to the orchestrator but never completes. Eventually hits the timeout, but that wastes time.

**Design:**

Monitor worker output events for repetition patterns and kill early.

**New module `src/supergod/orchestrator/stuck_detector.py`:**

```python
"""Detect stuck workers by monitoring output patterns."""

import logging
from collections import deque

log = logging.getLogger(__name__)

# Number of recent outputs to track per worker
WINDOW_SIZE = 10
# Fraction of identical outputs that triggers stuck detection
STUCK_THRESHOLD = 0.7


class StuckDetector:
    def __init__(self, window_size: int = WINDOW_SIZE, threshold: float = STUCK_THRESHOLD):
        self.window_size = window_size
        self.threshold = threshold
        self._buffers: dict[str, deque[str]] = {}  # subtask_id -> recent outputs

    def feed(self, subtask_id: str, output_text: str) -> bool:
        """Feed an output event. Returns True if the worker appears stuck."""
        if not output_text or output_text.startswith("["):
            return False  # skip control messages like [turn completed]

        if subtask_id not in self._buffers:
            self._buffers[subtask_id] = deque(maxlen=self.window_size)

        buf = self._buffers[subtask_id]
        normalized = output_text.strip()[:200]  # compare first 200 chars
        buf.append(normalized)

        if len(buf) < self.window_size:
            return False

        # Count most frequent output
        from collections import Counter
        counts = Counter(buf)
        most_common_count = counts.most_common(1)[0][1]
        ratio = most_common_count / len(buf)

        if ratio >= self.threshold:
            log.warning(
                "Stuck detected for subtask %s: %.0f%% of last %d outputs are identical",
                subtask_id, ratio * 100, len(buf),
            )
            return True
        return False

    def clear(self, subtask_id: str):
        self._buffers.pop(subtask_id, None)
```

**Integration in server.py:**

```python
# Global
stuck_detector = StuckDetector()

# In worker_ws handler, case "output":
output_text = _extract_text(msg.event)
if stuck_detector.feed(msg.task_id, output_text):
    log.warning("Worker %s stuck on %s, sending cancel", worker_name, msg.task_id)
    await wc.ws.send_text(serialize(WorkerCancelMsg(task_id=msg.task_id)))
    await scheduler.handle_task_error(worker_name, msg.task_id, "Stuck: repetitive output detected")
```

**Files modified:** `server.py` (+stuck detection on output events)
**New files:** `src/supergod/orchestrator/stuck_detector.py`
**Estimated effort:** Small

---

### Phase 5: UX and Extensibility (Features 13, 14, 15)

Final phase -- user experience and advanced coordination.

---

#### Feature 13: Four-Tier State Scoping
**Source:** ADK prefixed keys
**Priority:** NICE TO HAVE
**Dependencies:** Feature 4 (TaskState)

**Problem:** All state is flat -- task-level only. Workers cannot store temporary state that disappears after their subtask, and there is no cross-task global state (e.g., "which files have been modified in this session").

**Design:**

Prefix-based state scoping in the context_vars system:

```python
# In task_state.py, add scoping logic:

class ScopedState:
    """Four-tier state with prefix-based scoping.

    Prefixes:
        temp:    -- lives only during current subtask execution, discarded after
        task:    -- scoped to parent task, shared across subtasks of same task
        worker:  -- scoped to the worker, persists across tasks on that worker
        global:  -- shared across all tasks and workers
    """

    def __init__(self, db: "StateDB"):
        self.db = db

    async def get(self, key: str, task_id: str = "", worker_name: str = "") -> Any:
        scope, bare_key = self._parse_key(key)
        match scope:
            case "temp":
                return self._temp_store.get(bare_key)
            case "task":
                return await self._get_task_state(task_id, bare_key)
            case "worker":
                return await self._get_worker_state(worker_name, bare_key)
            case "global":
                return await self._get_global_state(bare_key)

    async def set(self, key: str, value: Any, task_id: str = "", worker_name: str = "") -> None:
        scope, bare_key = self._parse_key(key)
        match scope:
            case "temp":
                self._temp_store[bare_key] = value
            case "task":
                await self._set_task_state(task_id, bare_key, value)
            case "worker":
                await self._set_worker_state(worker_name, bare_key, value)
            case "global":
                await self._set_global_state(bare_key, value)

    def _parse_key(self, key: str) -> tuple[str, str]:
        for prefix in ("temp:", "task:", "worker:", "global:"):
            if key.startswith(prefix):
                return prefix[:-1], key[len(prefix):]
        return "task", key  # default scope is task
```

**Schema addition for global/worker state:**

```sql
CREATE TABLE IF NOT EXISTS kv_store (
    scope TEXT NOT NULL,       -- 'global', 'worker', 'task'
    scope_id TEXT NOT NULL,    -- '' for global, worker_name for worker, task_id for task
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (scope, scope_id, key)
);
```

**Files modified:** `state.py` (new table + KV methods)
**New files:** extend `task_state.py` with ScopedState class
**Estimated effort:** Medium

---

#### Feature 14: Human-in-the-Loop with State Editing
**Source:** LangGraph breakpoints + state editing
**Priority:** NICE TO HAVE
**Dependencies:** Feature 1 (checkpoints)

**Problem:** Once a task is submitted, the user can only watch or cancel. No ability to pause, inspect intermediate state, modify the plan, and resume.

**Design:**

Add a `pause` mechanism and state inspection/editing via CLI.

**New protocol messages:**

```python
class ClientPauseMsg(BaseModel):
    type: str = "pause"
    task_id: str

class ClientResumeMsg(BaseModel):
    type: str = "resume"
    task_id: str

class ClientEditStateMsg(BaseModel):
    type: str = "edit_state"
    task_id: str
    updates: dict[str, Any] = {}  # state fields to modify

class TaskPausedMsg(BaseModel):
    type: str = "task_paused"
    task_id: str
    state: dict[str, Any] = {}  # current state for inspection

class TaskStateMsg(BaseModel):
    type: str = "task_state"
    task_id: str
    state: dict[str, Any] = {}
    subtasks: list[dict] = []
    checkpoint_step: str = ""
```

**New TaskStatus:**

```python
class TaskStatus(str, Enum):
    # ... existing ...
    PAUSED = "paused"  # NEW
```

**Server logic:**

```python
async def _handle_client_pause(ws: WebSocket, msg: ClientPauseMsg):
    task_id = msg.task_id
    task = await db.get_task(task_id)
    if not task or task["status"] in (TaskStatus.COMPLETED, TaskStatus.FAILED):
        return

    await db.update_task_status(task_id, TaskStatus.PAUSED)
    # Don't cancel running subtasks -- let them finish, just don't assign new ones

    subtasks = await db.get_subtasks_for_task(task_id)
    state_json = task.get("state_json", "{}")

    await ws.send_text(serialize(TaskPausedMsg(
        task_id=task_id,
        state=json.loads(state_json),
    )))

async def _handle_client_resume(ws: WebSocket, msg: ClientResumeMsg):
    task_id = msg.task_id
    await db.update_task_status(task_id, TaskStatus.ASSIGNED)
    await scheduler.try_assign_ready_subtasks(task_id)

async def _handle_client_edit_state(ws: WebSocket, msg: ClientEditStateMsg):
    task = await db.get_task(msg.task_id)
    if task["status"] != TaskStatus.PAUSED:
        return  # only allow editing when paused

    # Apply edits to task state
    current_state = TaskState(**json.loads(task.get("state_json", "{}")))
    updated = current_state.apply_update(msg.updates)
    await db.execute(
        "UPDATE tasks SET state_json = ? WHERE task_id = ?",
        (updated.model_dump_json(), msg.task_id),
    )
```

**Guard in scheduler.py -- don't assign if paused:**

```python
async def try_assign_ready_subtasks(self, task_id: str) -> int:
    # Check if task is paused
    task = await self.db.get_task(task_id)
    if task and task["status"] == TaskStatus.PAUSED:
        log.info("Task %s is paused, not assigning", task_id)
        return 0
    # ... existing logic ...
```

**CLI commands (in cli.py):**

```python
@cli.command()
@click.argument("task_id")
def pause(task_id):
    """Pause a task -- stop assigning new subtasks."""
    send_msg(ClientPauseMsg(task_id=task_id))

@cli.command()
@click.argument("task_id")
def resume(task_id):
    """Resume a paused task."""
    send_msg(ClientResumeMsg(task_id=task_id))

@cli.command()
@click.argument("task_id")
@click.option("--set", "updates", multiple=True, help="key=value pairs to edit")
def edit(task_id, updates):
    """Edit task state while paused."""
    update_dict = dict(kv.split("=", 1) for kv in updates)
    send_msg(ClientEditStateMsg(task_id=task_id, updates=update_dict))
```

**Files modified:** `protocol.py` (+4 messages, +1 status), `server.py` (+3 handlers), `scheduler.py` (pause guard), `cli.py` (+3 commands)
**Estimated effort:** Large

---

#### Feature 15: Worker-to-Worker Handoff
**Source:** AutoGen Swarm handoff
**Priority:** NICE TO HAVE
**Dependencies:** Feature 5 (capabilities)

**Problem:** Tightly-coupled subtasks (e.g., "write the function" then "write the tests for that function") currently go through the full orchestrator roundtrip. The second worker has to re-read the first worker's output from git.

**Design:**

Allow a worker to directly hand off to another worker with context, using the orchestrator as a relay (not direct connections -- maintaining the hub topology).

**New protocol messages:**

```python
class WorkerHandoffMsg(BaseModel):
    type: str = "handoff"
    from_subtask_id: str
    to_capability: str           # e.g. "testing" -- orchestrator picks a worker with this capability
    prompt: str                  # what the receiving worker should do
    context: dict[str, Any] = {} # data from source worker (file contents, function signatures, etc.)
    branch: str                  # git branch with the work so far

class HandoffAcceptedMsg(BaseModel):
    type: str = "handoff_accepted"
    from_subtask_id: str
    new_subtask_id: str
    assigned_worker: str
```

**Server-side relay logic:**

```python
# In worker_ws handler, add case:
case "handoff":
    new_subtask_id = f"{msg.from_subtask_id}-handoff-{new_id()}"
    task_id = msg.from_subtask_id.rsplit("-", 1)[0]  # extract parent task

    # Create the handoff subtask
    await db.create_subtask(
        subtask_id=new_subtask_id,
        task_id=task_id,
        prompt=msg.prompt,
        branch=msg.branch,
        depends_on=[msg.from_subtask_id],
        context_vars={"handoff_context": msg.context, "handoff_from": worker_name},
    )

    # Find a worker with the requested capability
    candidates = [w for w in scheduler.get_idle_workers()
                  if msg.to_capability in w.capabilities]
    if candidates:
        assigned = await scheduler.assign_subtask(
            {"subtask_id": new_subtask_id, "prompt": msg.prompt, "branch": msg.branch},
            task_id,
        )
        await ws.send(serialize(HandoffAcceptedMsg(
            from_subtask_id=msg.from_subtask_id,
            new_subtask_id=new_subtask_id,
            assigned_worker=assigned or "queued",
        )))
    else:
        # No worker with capability available -- queue it
        await ws.send(serialize(HandoffAcceptedMsg(
            from_subtask_id=msg.from_subtask_id,
            new_subtask_id=new_subtask_id,
            assigned_worker="queued",
        )))
```

**Worker-side (daemon.py) -- emit handoff:**

```python
# Workers can emit handoff at the end of task execution:
async def _execute_task(self, ws, msg):
    # ... existing execution ...

    # If context_vars say this task should hand off:
    handoff_to = msg.context_vars.get("handoff_to")
    if handoff_to:
        await self._safe_send(ws, serialize(WorkerHandoffMsg(
            from_subtask_id=msg.id,
            to_capability=handoff_to["capability"],
            prompt=handoff_to["prompt"],
            context=handoff_to.get("context", {}),
            branch=msg.branch,
        )))
```

**Files modified:** `protocol.py` (+2 messages), `server.py` (+handoff handler), `daemon.py` (optional handoff emission)
**Estimated effort:** Medium

---

## Dependency Graph (Feature-to-Feature)

```
Phase 1 (Resilience):
  F1 Checkpoint ──────────────────────────┐
  F2 DAG + Cascade ───────────────────────┤
  F11 State Context Manager ──(needs F1)──┘

Phase 2 (Scheduling):
  F3 Fan-out/Fan-in ──(needs F2)──┐
  F5 LLM Router ─────────────────┤
  F8 Context Vars ────────────────┘

Phase 3 (Quality):
  F4 Typed State ──(needs F3 reducers)──┐
  F9 Validation Gates ─────────────────┤
  F12 Error Traces ──(needs F8)────────┘

Phase 4 (Intelligence):
  F6 Strategies ──(needs F2, F3)──┐
  F7 Planning Interval ──(needs F1)┤
  F10 Stuck Detection ────────────┘

Phase 5 (UX):
  F13 Scoped State ──(needs F4)──┐
  F14 Human-in-Loop ──(needs F1)─┤
  F15 Worker Handoff ──(needs F5)┘
```

## Implementation Order (Recommended)

```
Wave 1 (parallel):   F2 (cascade), F8 (context vars), F10 (stuck detection)
Wave 2 (parallel):   F1 (checkpoints), F9 (validation gates)
Wave 3 (sequential): F11 (state context -- needs F1)
Wave 4 (parallel):   F5 (LLM router), F12 (error traces -- needs F8)
Wave 5 (parallel):   F3 (fan-out -- needs F2), F4 (typed state)
Wave 6 (parallel):   F6 (strategies -- needs F2,F3), F7 (planning interval -- needs F1)
Wave 7 (parallel):   F13 (scoped state), F14 (human-in-loop), F15 (handoff)
```

## Summary of All File Changes

| File | Changes |
|------|---------|
| `src/supergod/shared/protocol.py` | +BLOCKED/PAUSED status, +WorkerReadyMsg capabilities, +6 new message types (pause/resume/edit_state/handoff/etc.) |
| `src/supergod/shared/config.py` | +PLANNING_INTERVAL, +ENABLE_LLM_ROUTING |
| `src/supergod/orchestrator/state.py` | +checkpoints table, +kv_store table, +context_vars/reducer/state_json columns, +8 new methods |
| `src/supergod/orchestrator/brain.py` | +fan_out field on Subtask, +strategy output from decomposition, +replan_check() |
| `src/supergod/orchestrator/scheduler.py` | +capabilities on WorkerConnection, +prefilter/llm_select, +cascade in handle_task_error, +retry_with_error, +pause guard |
| `src/supergod/orchestrator/server.py` | +checkpoint saves, +startup resume, +stuck detection, +validation gates, +replan hook, +pause/resume/edit handlers, +handoff relay |
| `src/supergod/worker/daemon.py` | +read context_vars, +optional handoff emission |
| `src/supergod/client/cli.py` | +pause/resume/edit commands |

| New File | Purpose |
|----------|---------|
| `src/supergod/orchestrator/reducers.py` | Typed merge reducer registry (replace, append, merge, union) |
| `src/supergod/orchestrator/task_state.py` | TaskState Pydantic model with per-field reducers + ScopedState |
| `src/supergod/orchestrator/strategies.py` | Swappable orchestration patterns (Concurrent, Sequential, Broadcast) |
| `src/supergod/orchestrator/validation.py` | Validation gate pipeline (diff check, syntax check, secrets scan) |
| `src/supergod/orchestrator/stuck_detector.py` | Repetitive output pattern detection |

## Estimated Total Effort

- Phase 1 (Resilience): 3-4 days
- Phase 2 (Scheduling): 2-3 days
- Phase 3 (Quality): 2-3 days
- Phase 4 (Intelligence): 2-3 days
- Phase 5 (UX): 3-4 days
- **Total: 12-17 days**

Phases 1-3 deliver the most value. Phase 4-5 are refinements. A viable MVP is Phases 1-3 (7-10 days).
