"""Tests for the message protocol."""

import pytest

from supergod.shared.protocol import (
    ClientTaskMsg,
    ProgressMsg,
    WorkerReadyMsg,
    WorkerTaskMsg,
    deserialize,
    serialize,
)


def test_serialize_client_task():
    msg = ClientTaskMsg(prompt="build user auth", task_id="test-123")
    raw = serialize(msg)
    assert '"type":"task"' in raw or '"type": "task"' in raw
    assert "build user auth" in raw


def test_deserialize_client_task():
    raw = '{"type": "task", "prompt": "build user auth", "task_id": "test-123"}'
    msg = deserialize(raw)
    assert isinstance(msg, ClientTaskMsg)
    assert msg.prompt == "build user auth"
    assert msg.task_id == "test-123"


def test_roundtrip_worker_ready():
    original = WorkerReadyMsg(name="worker-1")
    raw = serialize(original)
    restored = deserialize(raw)
    assert isinstance(restored, WorkerReadyMsg)
    assert restored.name == "worker-1"


def test_roundtrip_worker_task():
    original = WorkerTaskMsg(
        id="sub-001",
        prompt="implement auth",
        branch="task/sub-001",
    )
    raw = serialize(original)
    restored = deserialize(raw)
    assert isinstance(restored, WorkerTaskMsg)
    assert restored.id == "sub-001"
    assert restored.branch == "task/sub-001"


def test_roundtrip_progress():
    original = ProgressMsg(
        task_id="t1",
        subtask_id="t1-1",
        worker="worker-1",
        output="Working on auth...",
    )
    raw = serialize(original)
    restored = deserialize(raw)
    assert isinstance(restored, ProgressMsg)
    assert restored.worker == "worker-1"


def test_deserialize_unknown_type():
    with pytest.raises(ValueError, match="Unknown message type"):
        deserialize('{"type": "nonexistent"}')


def test_auto_generated_task_id():
    msg = ClientTaskMsg(prompt="test")
    assert len(msg.task_id) == 12  # hex uuid slice
