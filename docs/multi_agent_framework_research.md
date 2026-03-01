# Multi-Agent Framework Research — Feature Analysis for Supergod

**Date:** 2026-02-28
**Purpose:** Identify the most valuable features from 10 open-source multi-agent frameworks to steal for supergod.

---

## Framework Summaries

### 1. microsoft/autogen
**Architecture:** Actor-model runtime with message broadcasting. Three layers: Core (distributed runtime), AgentChat (team patterns), Extensions (LLM clients, MCP).

**Top features:**
- **SelectorGroupChat** — LLM reads agent descriptions + conversation context and dynamically picks the next speaker each turn. Combines rule-based filtering with LLM selection.
- **Swarm handoff** — Agents use tool calls to hand off to other agents directly, decentralized.
- **Full state serialization** — `save_state()` / `load_state()` on agents and entire teams. JSON-serializable, persist to disk.
- **Agent-as-tool wrapping** — Any agent can be wrapped as a tool for another agent (hierarchical delegation).
- **Custom candidate filtering** — Before LLM picks a worker, filter down to eligible ones by load/capabilities/availability.

**Weakness:** No automatic retry/recovery. If an agent fails, you handle it yourself.

---

### 2. langchain-ai/langgraph
**Architecture:** Graph-based execution (Pregel-inspired). Nodes = functions, edges = routing, state = typed dict with reducers per key.

**Top features:**
- **Checkpoint with time-travel** — Every execution step is checkpointed. Replay from any point, fork execution, inspect historical state. Production backends: SQLite, Postgres.
- **Fault tolerance via checkpoint recovery** — On failure, restart from last successful step. Pending writes from successful nodes preserved.
- **Human-in-the-loop with state editing** — Pause at any node, inspect state, modify with `update_state()`, resume. Choose which node executes next.
- **Send API for dynamic fan-out** — Conditional edge returns multiple `Send` objects → dynamic parallelism. N items through same node concurrently, N determined at runtime.
- **Typed state with reducers** — Each state key has its own merge strategy (overwrite, append, custom function). Prevents merge conflicts in shared state.
- **Cross-thread memory store** — Namespaced key-value storage that persists across threads. Supports semantic search.

**Weakness:** Single-process. No native distributed execution.

---

### 3. openai/swarm
**Architecture:** Radically minimal. Two primitives: Agents (instructions + functions) and Handoffs (return an Agent from a function = switch active agent). Single-threaded loop.

**Top features:**
- **Context variables (hidden from model)** — Dict that travels with tasks but is NOT sent to the LLM. Perfect for carrying metadata without bloating prompts.
- **Function-as-handoff** — A tool returning another Agent = control transfer. Zero boilerplate.
- **Deep-copy isolation** — Every `run()` deep-copies agent, messages, context. Prevents mutation leaks.
- **Streaming delimiters** — `{"delim":"start/end"}` markers per agent response. Easy to build UIs showing which agent is speaking.
- **max_turns safety valve** — Hard limit on execution steps.

**Weakness:** Educational only. No state, no persistence, no scale. Replaced by OpenAI Agents SDK.

---

### 4. microsoft/semantic-kernel
**Architecture:** Enterprise SDK. Kernel (DI container) + typed Agents + five orchestration patterns + InProcessRuntime.

**Top features:**
- **Five orchestration patterns as first-class primitives** — Concurrent (fan-out/fan-in), Sequential (pipeline), Handoff (dynamic routing), GroupChat (multi-agent conversation), Magentic (MagenticOne from MS Research).
- **OrchestrationHandoffs with transition rules** — Explicit graph of allowed agent-to-agent transitions. Constrains routing to valid paths.
- **Human-in-the-loop as first-class** — `InteractiveCallback` / `human_response_function` pauses execution for human input.
- **Declarative agent specs (YAML)** — Agents defined in YAML, instantiated via registry. Config-driven topology.
- **Unified invoke interface** — All patterns share `invoke(task, runtime) -> result`. Swap strategies without changing caller.

**Weakness:** Single-process `InProcessRuntime`. No distributed execution out of the box.

---

### 5. agno-agi/agno
**Architecture:** Three-layer platform: Framework (agents) + Runtime (stateless FastAPI) + Control Plane (monitoring UI). Teams with leader + members.

**Top features:**
- **Four TeamMode patterns** — `Coordinate` (supervisor assigns), `Route` (dispatch to one specialist), `Broadcast` (same task to ALL members), `Tasks` (leader decomposes into DAG with dependencies).
- **Task dependency DAG with cascade failures** — `Task` model with `dependencies`, status enum, `all_terminal()`, `_update_blocked_statuses()`. If a dependency fails, dependents auto-fail.
- **Parallel task execution** — `ThreadPoolExecutor` for concurrent tasks, deep-copy state before dispatch, merge after.
- **Streaming + pause/resume** — Agents pause mid-execution for human approval, resume later.
- **Session isolation with per-user memory** — Scoped by user_id + session_id. No cross-contamination.

**Weakness:** Teams run in one process. Not distributed across machines.

---

### 6. huggingface/smolagents
**Architecture:** ReAct loop (~1000 lines). Code-as-action: agents write Python code instead of JSON tool calls.

**Top features:**
- **Code-as-action** — Agents write executable Python. 30% fewer steps than JSON tool calls. Loops, conditionals, variable reuse, composition — all native.
- **Managed agents as callable functions** — Sub-agents appear as `agent_name(task="...")` in manager's code. No special delegation protocol.
- **Planning interval** — Every N steps, agent pauses to reassess: update known facts, reflect on next steps, re-plan. Prevents drift.
- **`final_answer_checks`** — Validation functions run when agent tries to return answer. If any fails, agent must continue. Quality gate.
- **Error traces in memory for self-correction** — Failed code traceback added to memory. LLM reads its own mistakes and fixes them.

**Weakness:** Single process, no persistence, in-memory only. Process death = total loss.

---

### 7. google/adk-python
**Architecture:** Hierarchical agent tree. Three agent types: LlmAgent, Workflow Agents (Sequential/Parallel/Loop), Custom.

**Top features:**
- **Workflow primitives: SequentialAgent, ParallelAgent, LoopAgent** — Deterministic, non-LLM orchestrators. Nest arbitrarily.
- **Four-tier state system** — Prefixed keys: no prefix (session), `user:` (cross-session per user), `app:` (global), `temp:` (current invocation only).
- **Session rewind** — Checkpoint/rollback to before a previous invocation.
- **Template injection in instructions** — Agent instructions reference state with `{key}` syntax, auto-substituted.
- **Event-sourced state** — All mutations via Events. Full audit trail and replay.

**Weakness:** No built-in error recovery. If a sub-agent fails, ADK has no retry/fallback.

---

### 8. openagents-org/openagents
**Architecture:** Network-based community model. Persistent networks where agents join/leave dynamically. Event-driven.

**Top features:**
- **Native MCP + A2A protocol support** — Both Model Context Protocol and Agent2Agent Protocol. Agents from different frameworks can participate in same network.
- **Persistent agent networks** — Networks live indefinitely. State, knowledge, relationships survive across sessions.
- **Mod/plugin architecture** — Snap in mods for delegation, artifacts, workspace mgmt, wikis. Extensible without code changes.
- **Multi-transport** — WebSocket, gRPC, HTTP, libp2p simultaneously. Agents choose their transport.
- **Dynamic agent discovery** — Agents register capabilities on connect, orchestrator routes by discovered capabilities.

**Weakness:** Weak error handling. No retry, no failure propagation.

---

### 9. foundationagents/openmanus
**Architecture:** Agent class hierarchy with PlanningFlow. ReAct loop with tool calling. Sequential plan execution.

**Top features:**
- **State context manager** — `state_context()` saves previous state, transitions, auto-reverts on exception through ERROR state. Prevents state corruption.
- **Stuck detection** — Checks for duplicate consecutive messages. Prevents infinite LLM loops.
- **PlanningFlow with agent routing** — Steps tagged with `[AGENT_NAME]` for routing to specialists. Fallback ordering configurable.
- **Multi-layered error handling** — Separate handling for token limits, JSON parse failures, invalid tools, execution errors. Each returns formatted error to LLM.
- **MCP tool integration** — Dynamic tool loading from remote MCP servers at runtime.

**Weakness:** Single process, sequential only. No parallel execution. No persistence.

---

### 10. deepset-ai/haystack
**Architecture:** Directed multigraph pipeline. Typed components connected with validated edges. Agents are components that loop internally.

**Top features:**
- **Typed connection validation** — `pipeline.connect()` validates output→input type compatibility at build time. Catches wiring bugs before execution.
- **State schema with merge handlers** — `merge_lists` (append), `replace_values` (overwrite), custom handlers. Declarative shared state management.
- **Pipeline looping with caps** — Components loop back to earlier components. Self-correction built into the graph.
- **AsyncPipeline parallel branches** — Independent branches execute concurrently automatically.
- **SuperComponents** — Wrap a pipeline as a single reusable component inside other pipelines.

**Weakness:** No distributed execution. Pipeline stops on component exception with no automatic retry.

---

## Feature Comparison Matrix

| Feature | AutoGen | LangGraph | Swarm | SK | Agno | smolagents | ADK | OpenAgents | OpenManus | Haystack |
|---------|---------|-----------|-------|----|------|------------|-----|------------|-----------|----------|
| Checkpoint/Resume | Manual | Auto+TimeTravel | No | No | No | No | Rewind | No | No | No |
| Parallel Execution | Yes | Send API | No | Concurrent | ThreadPool | No | ParallelAgent | Yes | No | AsyncPipeline |
| Task DAG w/deps | No | Graph edges | No | No | Yes+Cascade | No | Sequential/Parallel | No | Sequential | Pipeline graph |
| Error Recovery | Manual | Checkpoint restart | Append to chat | Manual | Cascade fail | Error in memory | escalate flag | Agent reconnect | State revert | raise flag |
| Human-in-Loop | UserProxy | Breakpoints+Edit | No | InteractiveCallback | Pause/Resume | No | Tool confirmation | No | No | No |
| State Persistence | JSON save/load | SQLite/Postgres | None | ThreadDB | SQLite | None | Event-sourced | Network-level | None | Per-execution |
| Dynamic Routing | LLM selector | Conditional edges | Function handoff | Handoff pattern | Route mode | Manager delegates | Agent transfer | Router agent | Tag routing | Routing components |
| Typed State | No | Pydantic+Reducers | No | No | Pydantic | No | Prefixed keys | No | Pydantic | Typed+Handlers |
| Config-Driven | No | No | No | YAML specs | No | No | No | YAML | No | No |
| Distributed | "Supported" | No (Platform=$) | No | No | Stateless runtime | No | Cloud Run | libp2p | No | No |

---

## Top 15 Features to Steal (Ranked by Value for Supergod)

### Tier 1: Must Have
1. **Checkpoint + Resume on every state transition** (LangGraph) — Save to SQLite after every task/subtask status change. Orchestrator crash → resume from last checkpoint.
2. **Task DAG with dependency tracking + cascade failures** (Agno) — Subtasks have explicit dependencies. If dependency fails, dependents auto-fail. `all_terminal()` check.
3. **Dynamic fan-out/fan-in** (LangGraph Send API) — Decompose into N subtasks at runtime, dispatch all in parallel, merge results with typed reducers.
4. **Typed state with merge reducers** (LangGraph + Haystack) — Define how concurrent worker results merge. Append for logs, overwrite for final answer, custom for code.

### Tier 2: High Value
5. **LLM-as-router for worker selection** (AutoGen SelectorGroupChat) — Brain reads worker descriptions + task context, picks best worker. Combine with rule-based pre-filtering.
6. **Orchestration patterns as swappable strategies** (Semantic Kernel) — `ConcurrentDispatch`, `SequentialPipeline`, `HandoffRouting` as interchangeable classes.
7. **Planning interval / periodic re-evaluation** (smolagents) — Every N completed subtasks, brain re-evaluates overall plan. Catch drift, re-prioritize.
8. **Context variables hidden from model** (Swarm) — Carry worker IDs, branch names, git state through orchestrator without sending to Codex.
9. **`final_answer_checks` / validation gates** (smolagents) — Before accepting worker output: tests pass? diff non-empty? touches right files?

### Tier 3: Nice to Have
10. **Stuck detection** (OpenManus) — Monitor worker output for repetitive patterns. Kill and reassign if Codex loops.
11. **State context manager with auto-revert** (OpenManus) — Wrap worker state transitions. Auto-revert to previous state on exception.
12. **Error traces fed back for self-correction** (smolagents) — When worker fails, full error in retry prompt. Most effective single recovery mechanism.
13. **Four-tier state scoping** (ADK) — `temp:` (this subtask), `task:` (parent task), `worker:` (this worker), `global:` (all tasks).
14. **Human-in-the-loop with state editing** (LangGraph) — Pause execution, user inspects/modifies state via CLI, resume.
15. **Worker-to-worker handoff** (AutoGen Swarm) — Workers on tightly-coupled subtasks hand off directly over WebSocket, skip orchestrator.

---

## Conflict & Synergy Analysis

### Features to SKIP (conflict with supergod architecture)
- **#4 Typed merge reducers** — Our merge is `git merge`, not dict merging. SQLite relational model is better for our queries.
- **#6 Swappable strategies** — Premature abstraction. We have ONE flow (fan-out/fan-in) and it works.
- **#13 Four-tier state scoping** — Conflicts with relational model. Our SQL tables already provide equivalent scoping.
- **#15 Worker-to-worker handoff** — Workers are on different servers behind NAT. Direct connections are impossible without relay (defeating purpose). Saves 50ms on 5-10 minute tasks.
- **#3 Dynamic fan-out** — Redundant with #2 (task DAG). A DAG with no dependencies IS fan-out.
- **#11 State auto-revert** — Subset of #1 (checkpoint/resume). Checkpointing is strictly more powerful.

### Features to DEFER (not worth it yet)
- **#5 LLM-as-router** — Workers are currently identical (same Codex binary, same model). Spending a Codex call to pick between identical workers wastes time. Only invest when workers are heterogeneous.
- **#7 Planning interval** — Re-evaluation adds 40% overhead for small tasks (<10 subtasks). Only worth it for large decompositions.
- **#14 Human-in-the-loop** — A simple `--human-approve` CLI flag before merge suffices. Full state editing is overkill.

### Features ALREADY DONE
- **#8 Context variables** — Protocol messages already carry metadata (branch, workdir, task IDs) that aren't sent to Codex.

### MVP Feature Set (implement these 5)
1. **#1 Checkpoint/Resume** — Orchestrator crash recovery. Scan for in-progress tasks on startup, resume from last checkpoint. ~50-100 lines.
2. **#2 Cascade failures** — When subtask fails, auto-fail dependents via BFS. Prevents stuck-forever subtasks. ~30-50 lines.
3. **#9 Validation gates** — Before accepting worker output: non-empty commit? non-empty diff? tests pass? ~20-40 lines.
4. **#10 Stuck detection** — Monitor worker output for repetitive patterns. Kill looping Codex and reassign. ~50-80 lines.
5. **#12 Error traces in retry** — Prepend previous error to retry prompt. Single most effective recovery mechanism. ~15-30 lines.

### Features UNIQUE to Supergod (not in any framework)
- **Git merge conflict resolution** — Pre-decomposition file partitioning + post-merge conflict detection + Codex-powered resolution
- **Codex account/session management** — Track which account each worker uses, detect rate limits, rotate accounts
- **Worker environment drift** — Verify git repo sync, Codex version, workspace cleanliness before assignment
- **Cost tracking** — Per-worker invocation counting, per-task cost estimation, budget caps
- **Partial result salvaging** — If 4/5 subtasks succeed, merge the 4 good branches instead of failing the entire task
- **Idempotency on crash** — If orchestrator re-assigns a completed subtask, worker should detect existing branch and skip

---

## Architecture Decisions (2026-02-28)

### All servers are identical (homogeneous)
Every server has every agent, every specialization, every tool. No "backend server" or "frontend server." Any worker can handle any task. Benefits:
- No wasted capacity (if 10 backend tasks come in and 0 frontend, all 15 workers help)
- If one server hits rate limits, overflow goes to any other server
- One daily sync script pushes updated configs to all machines
- Round-robin assignment is fine — no need for smart routing

### Linux, not Windows
Current server (88.99.142.89) is Windows and caused constant issues (.cmd wrappers, PATH hacks, batch files, scheduled tasks). All new servers will be Ubuntu on Hetzner CX22. Codex, Python, git all run natively. systemd handles process management.

### One brain, many workers
One orchestrator (Server 1) does all thinking — task decomposition, assignment, merging, testing. Workers just execute. Each server has a lightweight supervisor (systemd + cron health check) that babysits its local workers — restarts crashed processes, checks disk space, verifies Codex auth. No AI needed on the supervisor.

```
Server 1 (Orchestrator + Workers)
├── The Brain (FastAPI, decomposes/assigns/merges/tests)
├── systemd supervisor (restarts workers if crashed)
└── 3 workers

Servers 2-5 (Workers only)
├── systemd supervisor (restarts workers if crashed)
└── 3 workers each
```

## Related Docs
- Integration design with code examples: `docs/integration_design.md`
- Scaling plan (5 servers): `~/.claude/plans/frolicking-stirring-boot.md`
- Project overview for stakeholders: `docs/overview.md`
