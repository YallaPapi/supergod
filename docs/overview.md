# Supergod — Parallel AI at Scale

## What It Does

Supergod takes any task you'd give an AI assistant and distributes it across a team of AI agents working in parallel, across multiple servers, automatically.

This isn't limited to writing software. Anything you can ask a single AI to do — coding, research, analysis, data processing, content generation, file manipulation — supergod can break into pieces and run across 15 agents simultaneously. It's like having a team of AI workers instead of one.

The difference from a normal AI assistant: instead of one agent working sequentially (step 1, then step 2, then step 3...), supergod's brain figures out which steps are independent, assigns them to different agents, and runs them all at the same time. What takes one agent 30 minutes takes 15 agents 5-7 minutes.

**Software example:**
```
supergod run "Build a Python web scraper with HTTP retry logic, an HTML parser, CSV storage, and a CLI interface"
```
The brain breaks this into 5 independent coding tasks, 5 agents build simultaneously, code gets merged and tested automatically.

**Research example:**
```
supergod run "Analyze these 10 competitor websites and produce a comparison report covering pricing, features, and market positioning"
```
The brain assigns each competitor to a different agent, they all research in parallel, results get synthesized into one report.

**Any task that can be decomposed into independent pieces benefits from parallelization.**

Behind the scenes:
1. The orchestrator (the "brain") reads your request and breaks it into pieces
2. It assigns each piece to a different AI worker on a different server
3. All workers execute simultaneously
4. When all workers finish, the brain merges their outputs
5. For code: it runs the test suite to verify everything works
6. You get the result back in your terminal

---

## How It Works

### The Architecture

```
Your Laptop
    |
    |  "Build me a calculator app"
    |
    v
Server 1 — The Brain (Orchestrator)
    |  Breaks task into pieces, assigns work,
    |  merges code, runs tests
    |
    +----+----+----+----+
    |    |    |    |    |
    v    v    v    v    v
Server 1  Server 2  Server 3  Server 4  Server 5
3 workers 3 workers 3 workers 3 workers 3 workers
```

**15 AI agents working in parallel across 5 servers.**

### The Components

**Orchestrator (1 instance, on Server 1)**
- Receives tasks from your laptop over a WebSocket connection
- Uses its own AI instance to decompose tasks into subtasks
- Assigns subtasks to available workers
- Manages a shared code repository (git) that all workers push to
- Merges all branches when work is done
- Runs tests to verify the final product
- Reports results back to your laptop

**Workers (3 per server, 15 total)**
- Connect to the orchestrator and wait for assignments
- When given a subtask, spawn an AI coding session (Codex CLI)
- Stream progress back to the orchestrator in real-time
- Commit their code to a git branch when done
- Push to the shared repository

**Supervisor (1 per server, lightweight)**
- Not AI — just a systemd service + health check script
- Restarts workers if they crash
- Monitors disk space, checks AI authentication status
- Reports server health to the orchestrator

**CLI Client (your laptop)**
- Simple terminal tool: `supergod run "your task"`
- Shows live progress as workers code
- Also supports `supergod status`, `supergod watch`, `supergod cancel`

### Every Server Is Identical

All 5 servers have the same setup — same software, same AI tools, same capabilities. Any worker can handle any task. This means:
- No wasted capacity. If 10 backend tasks come in and 0 frontend tasks, all 15 workers pitch in.
- If one server hits rate limits, work automatically flows to others.
- Adding a new server is just cloning the setup. One script, done.

---

## The AI Engine

Supergod uses **Codex CLI** — OpenAI's command-line AI assistant. Each server is logged into a real Codex account (no API keys needed). Codex runs in a headless mode (`codex exec`) that:
- Takes any task as input
- Can read, write, and manipulate files in a local workspace
- Can run shell commands, access the internet, install packages
- Outputs structured progress events (JSON)
- Runs autonomously without human interaction

The orchestrator also uses Codex for "thinking" — breaking tasks into subtasks and evaluating results. This means the entire system is AI-powered end to end: AI plans the work, AI does the work, AI checks the work.

Codex is a general-purpose AI assistant, not just a code generator. Any task you'd give it in a terminal — writing code, analyzing data, generating reports, processing files, researching topics — can be parallelized across the worker fleet.

---

## Safety and Reliability

We studied 10 open-source multi-agent frameworks (from Microsoft, Google, OpenAI, Hugging Face, and others) and adopted the best patterns:

**Crash recovery** — Every state change is checkpointed to a database. If the orchestrator crashes, it resumes exactly where it left off when it restarts. No work is lost.

**Cascade failure handling** — If subtask A fails and subtask B depends on A, then B is automatically cancelled. The system doesn't waste time on work that can never succeed.

**Output validation** — Before final merge, completed subtasks are validated: commit SHA must be present, remote branch references must resolve, and branch head must differ from `main` when remote metadata is available.

**Stuck detection** — If an AI worker starts repeating itself (a known failure mode), the system detects it, kills the stuck worker, and reassigns the task to another worker.

**Error-informed retries** — When retrying a failed task, the previous error message is included in the new instructions. The AI learns from its own mistakes instead of repeating them.

---

## Infrastructure

**Servers:** Hetzner Cloud CX22 (Ubuntu Linux)
- 2 vCPU, 4GB RAM, 40GB disk
- €4.35/month each
- 5 servers = **€21.75/month total**

The servers don't need to be powerful. The AI processing happens on OpenAI's infrastructure. Our servers just run a lightweight Python program that coordinates the work — like a dispatcher at a taxi company doesn't need a fast car.

**Networking:**
- Workers connect outbound to the orchestrator over WebSocket
- No inbound ports needed on worker servers (simpler firewall, more secure)
- Your laptop connects to the orchestrator from anywhere

**Code coordination:**
- Shared git repository hosted on the orchestrator server
- Each subtask gets its own branch
- Automatic merge when all subtasks complete
- Tests run on the merged code before declaring success

---

## Current Status

**Working today:**
- Full pipeline: task submission -> decomposition -> parallel execution -> merge -> test -> result
- 3 workers on 1 server, proven with real coding tasks
- CLI client with live progress streaming
- WebSocket protocol for all communication

**Next steps:**
- Scale to 5 servers with shared git repository
- Add crash recovery, validation gates, stuck detection
- Automated server deployment (one-command setup for new servers)
- Codex account rotation and rate limit handling

---

## Cost Summary

| Item | Monthly Cost |
|------|-------------|
| 5x Hetzner CX22 servers | €21.75 |
| Codex CLI accounts | Free (usage limits per account) |
| **Total** | **~€22/month** |

For context: this gives you 15 parallel AI agents that can build software, conduct research, process data, and handle complex tasks in minutes instead of hours. The equivalent human team would cost thousands per month.

---

## Further Reading

- **Business overview & vision:** [`supergod_business.md`](../supergod_business.md) — What supergod replaces, who it's for, the customer experience
- **Framework research:** [`docs/multi_agent_framework_research.md`](multi_agent_framework_research.md) — Analysis of 10 open-source multi-agent frameworks and which features we adopted
- **Integration design:** [`docs/integration_design.md`](integration_design.md) — Detailed code-level implementation plan for reliability features
- **Skill library:** [docs/skills_library.md](skills_library.md) - Homogeneous workers with capability-pack prompt injection and curated skill imports
- **Mission control dashboard:** [docs/mission_control_dashboard.md](mission_control_dashboard.md) - Browser UI for worker heartbeat, task lanes, and event timeline
- **Ops hardening:** [docs/ops_hardening.md](ops_hardening.md) - Quoting-safe cluster operations, Git auto-sync, and anti-drift policy
