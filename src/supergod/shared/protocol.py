"""Message protocol for supergod WebSocket communication.

All messages are JSON objects with a "type" field that determines the schema.
"""

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Type, Union

from pydantic import BaseModel, Field


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Enums ---


class TaskStatus(str, Enum):
    PENDING = "pending"
    DECOMPOSING = "decomposing"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkerStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"


# --- Client → Orchestrator ---


class ClientTaskMsg(BaseModel):
    type: str = "task"
    prompt: str
    task_id: str = Field(default_factory=new_id)


class ClientStatusMsg(BaseModel):
    type: str = "status"


class ClientCancelMsg(BaseModel):
    type: str = "cancel"
    task_id: str


# --- Orchestrator → Client ---


class TaskAcceptedMsg(BaseModel):
    type: str = "task_accepted"
    task_id: str


class ProgressMsg(BaseModel):
    type: str = "progress"
    task_id: str
    subtask_id: Optional[str] = None
    worker: Optional[str] = None
    output: str = ""


class TaskCompleteMsg(BaseModel):
    type: str = "task_complete"
    task_id: str
    summary: str = ""


class TaskFailedMsg(BaseModel):
    type: str = "task_failed"
    task_id: str
    error: str = ""


class WorkerInfo(BaseModel):
    name: str
    status: WorkerStatus


class WorkerListMsg(BaseModel):
    type: str = "workers"
    list: List[WorkerInfo] = []


class TaskInfo(BaseModel):
    task_id: str
    status: TaskStatus
    prompt: str
    subtasks: int = 0
    completed_subtasks: int = 0


class StatusResponseMsg(BaseModel):
    type: str = "status_response"
    tasks: List[TaskInfo] = []
    workers: List[WorkerInfo] = []


# --- Orchestrator → Worker ---


class WorkerTaskMsg(BaseModel):
    type: str = "task"
    id: str
    prompt: str
    branch: str
    workdir: str = "/workspace"


class WorkerCancelMsg(BaseModel):
    type: str = "cancel"
    task_id: str


class PingMsg(BaseModel):
    type: str = "ping"


# --- Worker → Orchestrator ---


class WorkerReadyMsg(BaseModel):
    type: str = "ready"
    name: str


class WorkerOutputMsg(BaseModel):
    type: str = "output"
    task_id: str
    event: Dict[str, Any] = {}


class WorkerTaskCompleteMsg(BaseModel):
    type: str = "task_complete"
    task_id: str
    commit: str = ""


class WorkerTaskErrorMsg(BaseModel):
    type: str = "task_error"
    task_id: str
    error: str = ""


class PongMsg(BaseModel):
    type: str = "pong"


# --- Serialization ---

# All message types indexed by their "type" field
_MSG_TYPES: Dict[str, type] = {}


def _register(*classes: Type[BaseModel]) -> None:
    for cls in classes:
        type_val = cls.model_fields["type"].default
        _MSG_TYPES[type_val] = cls


# Client → Orchestrator (prefixed to avoid collisions)
_register(
    ClientTaskMsg,
    ClientStatusMsg,
    ClientCancelMsg,
)

# Orchestrator → Client
_register(
    TaskAcceptedMsg,
    ProgressMsg,
    TaskCompleteMsg,
    TaskFailedMsg,
    WorkerListMsg,
    StatusResponseMsg,
)

# Orchestrator → Worker
_register(
    WorkerTaskMsg,
    WorkerCancelMsg,
    PingMsg,
)

# Worker → Orchestrator
_register(
    WorkerReadyMsg,
    WorkerOutputMsg,
    WorkerTaskCompleteMsg,
    WorkerTaskErrorMsg,
    PongMsg,
)


def serialize(msg: BaseModel) -> str:
    return msg.model_dump_json()


def deserialize(raw: Union[str, bytes]) -> BaseModel:
    data = json.loads(raw)
    msg_type = data.get("type")
    if msg_type not in _MSG_TYPES:
        raise ValueError(f"Unknown message type: {msg_type}")
    return _MSG_TYPES[msg_type].model_validate(data)
