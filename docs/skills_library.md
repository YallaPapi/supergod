# Skill Library and Capability Packs

Supergod workers are intentionally homogeneous. Any worker can execute any subtask.
To avoid bottlenecks while still giving specialized behavior, each subtask prompt can
include a selected set of capability packs.

## What Is Implemented

- Curated pack model:
  - `core-dev`
  - `review-qa`
  - `orchestration`
  - `infra-ops`
  - `ml-media`
  - `project-i2v` (optional)
- Imported library from external Claude agents:
  - `src/supergod/skills/library/index.json`
  - `src/supergod/skills/library/agents/*.md`
- Runtime selector:
  - Picks packs based on task/subtask keywords.
  - Scores and injects top skills into each worker prompt.
  - Emits `skill_injection` task events for observability.

## Import or Refresh Skills

Use the CLI:

```bash
supergod import-skills --source "C:/Users/asus/Desktop/projects/i2v/.claude/agents"
```

Generic-only library (skip project-specific i2v pack):

```bash
supergod import-skills --source "C:/path/to/.claude/agents" --exclude-project-specific
```

## Force Packs Per Task

Add a line in your task prompt:

```text
packs: infra-ops,review-qa
```

This bypasses auto-selection and uses only the listed packs.

## Runtime Configuration

- `SUPERGOD_SKILLS_ENABLED` (default: `true`)
- `SUPERGOD_SKILLS_PROFILE` (default: `default`)
- `SUPERGOD_SKILLS_INCLUDE_PROJECT_SPECIFIC` (default: `true`)
- `SUPERGOD_SKILLS_MAX_SKILLS` (default: `6`)
- `SUPERGOD_SKILLS_MAX_CHARS` (default: `5000`)
