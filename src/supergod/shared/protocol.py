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
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class WorkerStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"


# --- Client → Orchestrator ---


class ClientTaskMsg(BaseModel):
    type: str = "task"
    prompt: str
    priority: int = 100
    task_id: str = Field(default_factory=new_id)


class ClientStatusMsg(BaseModel):
    type: str = "status"


class ClientCancelMsg(BaseModel):
    type: str = "cancel"
    task_id: str


class ClientPauseMsg(BaseModel):
    type: str = "pause"
    task_id: str


class ClientResumeMsg(BaseModel):
    type: str = "resume"
    task_id: str


class ClientChatMsg(BaseModel):
    type: str = "chat"
    session_id: str = Field(default_factory=new_id)
    message: str


class ClientStartFromBriefMsg(BaseModel):
    type: str = "start_from_brief"
    session_id: str


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


class ChatResponseMsg(BaseModel):
    type: str = "chat_response"
    session_id: str
    reply: str
    ready_to_start: bool = False
    draft_prompt: str = ""


class TaskReviewMsg(BaseModel):
    type: str = "task_review"
    task_id: str
    completed_count: int = 0
    failed_count: int = 0
    blocked_count: int = 0
    failed_subtasks: List[Dict[str, Any]] = []
    blocked_subtasks: List[Dict[str, Any]] = []
    test_summary: str = ""


class WorkerInfo(BaseModel):
    name: str
    status: WorkerStatus
    current_subtask: str | None = None
    last_seen: str | None = None


class WorkerListMsg(BaseModel):
    type: str = "workers"
    list: List[WorkerInfo] = []


class TaskInfo(BaseModel):
    task_id: str
    status: TaskStatus
    prompt: str
    priority: int = 100
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
    execution_token: str = ""
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
    execution_token: str = ""
    event: Dict[str, Any] = {}


class WorkerTaskCompleteMsg(BaseModel):
    type: str = "worker_task_complete"
    task_id: str
    execution_token: str = ""
    commit: str = ""


class WorkerTaskErrorMsg(BaseModel):
    type: str = "task_error"
    task_id: str
    execution_token: str = ""
    error: str = ""


class PongMsg(BaseModel):
    type: str = "pong"


# --- Serialization ---

# All message types indexed by their "type" field.
# Multiple models can share the same "type" string (e.g. client "task" vs worker "task"),
# so we store a list of candidates per type and try each during deserialization.
_MSG_TYPES: Dict[str, List[Type[BaseModel]]] = {}


def _register(*classes: Type[BaseModel]) -> None:
    for cls in classes:
        type_val = cls.model_fields["type"].default
        _MSG_TYPES.setdefault(type_val, []).append(cls)


# Client → Orchestrator (prefixed to avoid collisions)
_register(
    ClientTaskMsg,
    ClientStatusMsg,
    ClientCancelMsg,
    ClientPauseMsg,
    ClientResumeMsg,
    ClientChatMsg,
    ClientStartFromBriefMsg,
)

# Orchestrator → Client
_register(
    TaskAcceptedMsg,
    ProgressMsg,
    TaskCompleteMsg,
    TaskFailedMsg,
    ChatResponseMsg,
    TaskReviewMsg,
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
    candidates = _MSG_TYPES[msg_type]
    if len(candidates) == 1:
        return candidates[0].model_validate(data)
    # Multiple models share the same "type" string (e.g. client "task" vs worker "task").
    # Pick the candidate whose required fields best match the data keys.
    best: Optional[BaseModel] = None
    best_score = -1
    errors = []
    for cls in candidates:
        required = {
            name
            for name, info in cls.model_fields.items()
            if info.is_required()
        }
        # Score: how many of the model's required fields are present in data
        present = required & data.keys()
        if present != required:
            # Missing required fields — skip (will fail validation)
            continue
        score = len(required)
        if score > best_score:
            best_score = score
            try:
                best = cls.model_validate(data)
            except Exception as e:
                errors.append(e)
                best = None
    if best is not None:
        return best
    # Fallback: try all candidates
    for cls in candidates:
        try:
            return cls.model_validate(data)
        except Exception as e:
            errors.append(e)
    raise errors[-1]
