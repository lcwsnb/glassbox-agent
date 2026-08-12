"""Append-only SQLite storage and the pure event reducer."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .domain import (
    EventType,
    MemoryCapsule,
    RuntimeEvent,
    RuntimeState,
    Session,
    TodoItem,
    ToolCall,
    utc_now,
)


class EventStore:
    """SQLite event ledger.

    The public API contains no event update/delete operation. Session metadata may
    change, but historical runtime facts remain append-only.
    """

    def __init__(self, path: str | Path = ".glassbox/glassbox.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    parent_session_id TEXT REFERENCES sessions(id),
                    fork_event_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    turn_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(session_id, turn_id, sequence)
                );

                CREATE INDEX IF NOT EXISTS idx_events_session_id
                    ON events(session_id, id);
                CREATE INDEX IF NOT EXISTS idx_events_turn_id
                    ON events(session_id, turn_id, sequence);
                """
            )

    def create_session(
        self,
        title: str = "New session",
        *,
        parent_session_id: str | None = None,
        fork_event_id: int | None = None,
    ) -> Session:
        now = utc_now()
        session = Session(
            id=uuid.uuid4().hex[:12],
            title=title.strip() or "New session",
            parent_session_id=parent_session_id,
            fork_event_id=fork_event_id,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions
                    (id, title, parent_session_id, fork_event_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.title,
                    session.parent_session_id,
                    session.fork_event_id,
                    session.created_at.isoformat(),
                    session.updated_at.isoformat(),
                ),
            )
        return session

    def get_session(self, session_id: str) -> Session:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Session '{session_id}' does not exist")
        return self._row_to_session(row)

    def list_sessions(self) -> list[Session]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
        return [self._row_to_session(row) for row in rows]

    def append(self, event: RuntimeEvent) -> RuntimeEvent:
        self.get_session(event.session_id)
        created_at = event.created_at or utc_now()
        payload = json.dumps(event.payload, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            if event.sequence is None:
                row = connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
                    FROM events WHERE session_id = ? AND turn_id = ?
                    """,
                    (event.session_id, event.turn_id),
                ).fetchone()
                sequence = int(row["next_sequence"])
            else:
                sequence = event.sequence
            cursor = connection.execute(
                """
                INSERT INTO events
                    (session_id, turn_id, sequence, type, payload_json,
                     schema_version, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.session_id,
                    event.turn_id,
                    sequence,
                    event.event_type.value,
                    payload,
                    event.schema_version,
                    created_at.isoformat(),
                ),
            )
            connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (created_at.isoformat(), event.session_id),
            )
            event_id = int(cursor.lastrowid)
        return event.model_copy(
            update={"id": event_id, "sequence": sequence, "created_at": created_at}
        )

    def load_local_events(
        self, session_id: str, *, until_event_id: int | None = None
    ) -> list[RuntimeEvent]:
        sql = "SELECT * FROM events WHERE session_id = ?"
        params: list[Any] = [session_id]
        if until_event_id is not None:
            sql += " AND id <= ?"
            params.append(until_event_id)
        sql += " ORDER BY id ASC"
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._row_to_event(row) for row in rows]

    def load_history(self, session_id: str) -> list[RuntimeEvent]:
        self.get_session(session_id)
        return self._load_history(session_id, cutoff=None, seen=set())

    def _load_history(
        self, session_id: str, *, cutoff: int | None, seen: set[str]
    ) -> list[RuntimeEvent]:
        if session_id in seen:
            raise RuntimeError("Session ancestry contains a cycle")
        seen = {*seen, session_id}
        session = self.get_session(session_id)
        inherited: list[RuntimeEvent] = []
        if session.parent_session_id is not None:
            parent_cutoff = session.fork_event_id
            if cutoff is not None:
                parent_cutoff = min(parent_cutoff or cutoff, cutoff)
            inherited = self._load_history(
                session.parent_session_id, cutoff=parent_cutoff, seen=seen
            )
        local = self.load_local_events(session_id, until_event_id=cutoff)
        return [*inherited, *local]

    def fork_session(self, session_id: str, event_id: int, title: str | None = None) -> Session:
        source = self.get_session(session_id)
        history = self.load_history(session_id)
        checkpoint = next((event for event in history if event.id == event_id), None)
        if checkpoint is None:
            raise ValueError(f"Event {event_id} is not in session '{session_id}' history")
        if checkpoint.event_type is not EventType.ASSISTANT_MESSAGE:
            raise ValueError("Fork checkpoints must be completed assistant-message events")
        child = self.create_session(
            title=title or f"Fork of {source.title}",
            parent_session_id=session_id,
            fork_event_id=event_id,
        )
        self.append(
            RuntimeEvent(
                session_id=child.id,
                turn_id=f"fork-{uuid.uuid4().hex[:8]}",
                event_type=EventType.SESSION_FORKED,
                payload={"source_session_id": session_id, "fork_event_id": event_id},
            )
        )
        return child

    def export_jsonl(self, session_id: str) -> str:
        lines = []
        for event in self.load_history(session_id):
            value = event.model_dump(mode="json")
            value["event_type"] = event.event_type.value
            lines.append(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        return "\n".join(lines) + ("\n" if lines else "")

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> Session:
        return Session(
            id=row["id"],
            title=row["title"],
            parent_session_id=row["parent_session_id"],
            fork_event_id=row["fork_event_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> RuntimeEvent:
        return RuntimeEvent(
            id=row["id"],
            session_id=row["session_id"],
            turn_id=row["turn_id"],
            sequence=row["sequence"],
            event_type=EventType(row["type"]),
            payload=json.loads(row["payload_json"]),
            schema_version=row["schema_version"],
            created_at=row["created_at"],
        )


def _assistant_message(payload: dict[str, Any]) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": payload.get("content")}
    tool_calls = payload.get("tool_calls") or []
    if tool_calls:
        message["tool_calls"] = [
            {
                "id": call["id"],
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": call.get("raw_arguments")
                    or json.dumps(call.get("arguments", {}), ensure_ascii=False),
                },
            }
            for call in tool_calls
        ]
    return message


def _apply_todo_mutation(state: RuntimeState, data: dict[str, Any]) -> None:
    mutation = data.get("todo_mutation")
    if not mutation:
        return
    action = mutation.get("action")
    item_data = mutation.get("item")
    if action == "add" and item_data or action == "complete" and item_data:
        item = TodoItem.model_validate(item_data)
        state.todos[item.id] = item


def reduce_events(events: Iterable[RuntimeEvent]) -> RuntimeState:
    """Project immutable events into the current runtime state."""

    state = RuntimeState()
    for event in events:
        state.session_id = event.session_id
        state.event_count += 1
        payload = event.payload
        match event.event_type:
            case EventType.USER_MESSAGE:
                state.messages.append({"role": "user", "content": payload["content"]})
                state.user_turns += 1
                state.status = "running"
            case EventType.LLM_RESPONDED:
                state.messages.append(_assistant_message(payload))
                for call_data in payload.get("tool_calls") or []:
                    call = ToolCall.model_validate(call_data)
                    state.pending_tool_calls[call.id] = call
            case EventType.TOOL_REQUESTED:
                call = ToolCall.model_validate(payload["call"])
                state.pending_tool_calls[call.id] = call
            case EventType.TOOL_SUCCEEDED | EventType.TOOL_FAILED:
                state.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": payload["call_id"],
                        "content": payload["content"],
                    }
                )
                state.pending_tool_calls.pop(payload["call_id"], None)
                if event.event_type is EventType.TOOL_SUCCEEDED:
                    _apply_todo_mutation(state, payload.get("data") or {})
            case EventType.CONTEXT_COMPACTED:
                state.memory = MemoryCapsule.model_validate(payload["memory"])
            case EventType.ASSISTANT_MESSAGE:
                state.last_assistant_message = payload["content"]
                state.last_completed_turn = event.turn_id
                state.status = "completed"
            case EventType.RUN_STOPPED:
                state.status = payload.get("status", "stopped")
                for call_id in payload.get("pending_call_ids", []):
                    state.pending_tool_calls.pop(call_id, None)
            case _:
                pass
    return state


def event_messages(events: Iterable[RuntimeEvent], *, after_id: int | None = None) -> list[dict]:
    """Convert persisted conversation facts back to provider message objects."""

    messages: list[dict[str, Any]] = []
    for event in events:
        if after_id is not None and event.id is not None and event.id <= after_id:
            continue
        if event.event_type is EventType.USER_MESSAGE:
            messages.append({"role": "user", "content": event.payload["content"]})
        elif event.event_type is EventType.LLM_RESPONDED:
            messages.append(_assistant_message(event.payload))
        elif event.event_type in {EventType.TOOL_SUCCEEDED, EventType.TOOL_FAILED}:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": event.payload["call_id"],
                    "content": event.payload["content"],
                }
            )
    return messages
