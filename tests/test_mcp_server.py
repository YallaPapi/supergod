"""Tests for MCP server helpers and status rendering."""

import json

from supergod import mcp_server


class _FakeWS:
    def __init__(self, response_payload):
        self._payload = response_payload
        self.sent_messages = []

    async def send(self, raw: str):
        self.sent_messages.append(raw)

    async def recv(self):
        return json.dumps(self._payload)


class _FakeConnect:
    def __init__(self, ws):
        self._ws = ws

    async def __aenter__(self):
        return self._ws

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_with_auth_token_no_token_returns_original(monkeypatch):
    monkeypatch.setattr(mcp_server, "AUTH_TOKEN", "")
    url = "ws://example.com/ws/client"
    assert mcp_server._with_auth_token(url) == url


def test_with_auth_token_appends_query(monkeypatch):
    monkeypatch.setattr(mcp_server, "AUTH_TOKEN", "abc123")
    out = mcp_server._with_auth_token("ws://example.com/ws/client?foo=bar")
    assert out == "ws://example.com/ws/client?foo=bar&token=abc123"


def test_with_auth_token_preserves_existing_token(monkeypatch):
    monkeypatch.setattr(mcp_server, "AUTH_TOKEN", "override-me")
    out = mcp_server._with_auth_token("ws://example.com/ws/client?token=kept")
    assert out == "ws://example.com/ws/client?token=kept"


async def test_get_status_formats_workers_and_tasks(monkeypatch):
    ws = _FakeWS(
        {
            "workers": [{"name": "worker-a", "status": "idle"}],
            "tasks": [
                {
                    "task_id": "task-1",
                    "status": "running",
                    "completed_subtasks": 2,
                    "subtasks": 5,
                    "prompt": "Build a customer-acquisition marketing machine.",
                }
            ],
        }
    )
    monkeypatch.setattr(mcp_server.websockets, "connect", lambda *_a, **_k: _FakeConnect(ws))
    monkeypatch.setattr(mcp_server, "ORCHESTRATOR_URL", "ws://orchestrator:8080")

    status = await mcp_server._get_status()

    assert "Workers (1):" in status
    assert "worker-a: idle" in status
    assert "Tasks (1):" in status
    assert "[running] task-1 (2/5)" in status
    assert ws.sent_messages == ['{"type": "status"}']


async def test_get_status_connection_error(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(mcp_server.websockets, "connect", _boom)
    monkeypatch.setattr(mcp_server, "ORCHESTRATOR_URL", "ws://offline:9999")

    status = await mcp_server._get_status()
    assert "Cannot reach orchestrator at ws://offline:9999" in status
    assert "connection refused" in status
