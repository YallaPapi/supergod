# Mission Control Dashboard

The orchestrator now serves a browser-based mission control dashboard.

## Access

- Local:
  - `http://localhost:8080/mission`
- Remote:
  - `http://<orchestrator-ip>:8080/mission`

If `SUPERGOD_AUTH_TOKEN` is set, include:

- `http://<orchestrator-ip>:8080/mission?token=<token>`

## What It Shows

- Worker heartbeat cards:
  - worker status
  - last heartbeat age
  - current subtask
- Task progress lanes:
  - task status
  - subtasks completed/total
  - prompt preview
- Event timeline:
  - events pulled from `/task/{task_id}/events`
  - sorted latest-first
  - includes remediation events (for example `dependency_repair` when an invalid subtask DAG is auto-repaired)

## Data Sources

- `GET /snapshot` for consolidated tasks/workers
- `GET /task/{task_id}/events` for task timeline details
- Existing websocket/CLI flow is unchanged
