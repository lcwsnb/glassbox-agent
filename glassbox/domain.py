"""Domain types shared by the GlassBox runtime.

The event payload is deliberately JSON-shaped. SQLite stores facts, while this
module gives those facts explicit types at runtime boundaries.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class EventType(StrEnum):
    USER_MESSAGE = "user_message"
    LLM_REQUESTED = "llm_requested"
    LLM_RESPONDED = "llm_responded"
    TOOL_REQUESTED = "tool_requested"
    TOOL_SUCCEEDED = "tool_succeeded"
    TOOL_FAILED = "tool_failed"
    RETRY_SCHEDULED = "retry_scheduled"
    CONTEXT_COMPACTED = "context_compacted"
    CONTEXT_COMPACTION_FAILED = "context_compaction_failed"
    ASSISTANT_MESSAGE = "assistant_message"
    RUN_STOPPED = "run_stopped"
    SESSION_FORKED = "session_forked"


class Session(BaseModel):
    id: str
    title: str
    parent_session_id: str | None = None
    fork_event_id: int | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    raw_arguments: str | None = None
    parse_error: str | None = None


class ToolResult(BaseModel):
    call_id: str
    name: str
    ok: bool
    content: str
    data: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    retryable: bool = False
    duration_ms: int = 0


class ModelDecision(BaseModel):
    kind: Literal["final", "tool_calls"]
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    reasoning_content: str | None = Field(default=None, repr=False, exclude=True)


class RuntimeEvent(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    id: int | None = None
    session_id: str
    turn_id: str
    sequence: int | None = None
    event_type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)
    schema_version: int = 1
    created_at: datetime = Field(default_factory=utc_now)


class TodoItem(BaseModel):
    id: str
    title: str
    completed: bool = False
    created_at: str
    completed_at: str | None = None


class MemoryCapsule(BaseModel):
    goals: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    tool_facts: list[str] = Field(default_factory=list)
    open_items: list[str] = Field(default_factory=list)
    through_event_id: int | None = None


class RuntimeState(BaseModel):
    session_id: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    todos: dict[str, TodoItem] = Field(default_factory=dict)
    memory: MemoryCapsule = Field(default_factory=MemoryCapsule)
    pending_tool_calls: dict[str, ToolCall] = Field(default_factory=dict)
    last_assistant_message: str | None = None
    last_completed_turn: str | None = None
    status: Literal["idle", "running", "completed", "stopped", "failed"] = "idle"
    user_turns: int = 0
    event_count: int = 0


class RunOutcome(BaseModel):
    session_id: str
    turn_id: str
    status: Literal["completed", "stopped", "failed"]
    answer: str
    steps: int
    state: RuntimeState


class ContextSnapshot(BaseModel):
    messages: list[dict[str, Any]]
    estimated_chars: int
    memory: MemoryCapsule
    cutoff_event_id: int | None
    recent_turn_ids: list[str]
