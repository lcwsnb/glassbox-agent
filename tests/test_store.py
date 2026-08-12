from __future__ import annotations

import json

import pytest

from glassbox.domain import EventType, RuntimeEvent
from glassbox.store import EventStore, event_messages, reduce_events


def append(store: EventStore, session_id: str, turn: str, kind: EventType, payload: dict):
    return store.append(
        RuntimeEvent(session_id=session_id, turn_id=turn, event_type=kind, payload=payload)
    )


def test_store_reducer_and_jsonl_export(tmp_path) -> None:
    store = EventStore(tmp_path / "events.db")
    session = store.create_session("A")
    append(store, session.id, "t1", EventType.USER_MESSAGE, {"content": "hello"})
    append(
        store,
        session.id,
        "t1",
        EventType.LLM_RESPONDED,
        {"content": "hi", "tool_calls": [], "usage": {}},
    )
    final = append(store, session.id, "t1", EventType.ASSISTANT_MESSAGE, {"content": "hi"})
    state = reduce_events(store.load_history(session.id))
    assert state.messages == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    assert state.last_assistant_message == "hi"
    assert state.status == "completed"
    lines = store.export_jsonl(session.id).splitlines()
    assert len(lines) == 3
    assert json.loads(lines[-1])["id"] == final.id
    assert store.list_sessions()[0].id == session.id


def test_todo_projection_and_tool_message(tmp_path) -> None:
    store = EventStore(tmp_path / "events.db")
    session = store.create_session()
    item = {
        "id": "todo1",
        "title": "submit",
        "completed": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        "completed_at": None,
    }
    append(
        store,
        session.id,
        "t1",
        EventType.TOOL_SUCCEEDED,
        {
            "call_id": "c1",
            "name": "todo",
            "content": "ok",
            "data": {"todo_mutation": {"action": "add", "item": item}},
        },
    )
    state = reduce_events(store.load_history(session.id))
    assert state.todos["todo1"].title == "submit"
    assert state.messages[-1] == {"role": "tool", "tool_call_id": "c1", "content": "ok"}


def test_fork_inherits_prefix_then_diverges(tmp_path) -> None:
    store = EventStore(tmp_path / "events.db")
    parent = store.create_session("Parent")
    append(store, parent.id, "t1", EventType.USER_MESSAGE, {"content": "one"})
    checkpoint = append(
        store,
        parent.id,
        "t1",
        EventType.ASSISTANT_MESSAGE,
        {"content": "first"},
    )
    append(store, parent.id, "t2", EventType.USER_MESSAGE, {"content": "parent-only"})
    child = store.fork_session(parent.id, checkpoint.id or 0)
    append(store, child.id, "t3", EventType.USER_MESSAGE, {"content": "child-only"})
    child_history = store.load_history(child.id)
    contents = [event.payload.get("content") for event in child_history]
    assert "one" in contents and "child-only" in contents
    assert "parent-only" not in contents
    assert reduce_events(child_history).session_id == child.id
    with pytest.raises(ValueError, match="completed assistant"):
        store.fork_session(parent.id, 1)
    with pytest.raises(ValueError, match="not in session"):
        store.fork_session(parent.id, 9999)


def test_event_messages_preserve_tool_call_pairing(tmp_path) -> None:
    store = EventStore(tmp_path / "events.db")
    session = store.create_session()
    first = append(store, session.id, "t1", EventType.USER_MESSAGE, {"content": "calc"})
    append(
        store,
        session.id,
        "t1",
        EventType.LLM_RESPONDED,
        {
            "content": None,
            "tool_calls": [{"id": "c1", "name": "calculator", "arguments": {"expression": "2+2"}}],
        },
    )
    append(
        store,
        session.id,
        "t1",
        EventType.TOOL_SUCCEEDED,
        {"call_id": "c1", "name": "calculator", "content": "4", "data": {}},
    )
    messages = event_messages(store.load_history(session.id), after_id=first.id)
    assert messages[0]["tool_calls"][0]["id"] == "c1"
    assert messages[1]["tool_call_id"] == "c1"


def test_missing_session_is_actionable(tmp_path) -> None:
    store = EventStore(tmp_path / "events.db")
    with pytest.raises(KeyError, match="does not exist"):
        store.get_session("missing")


def test_store_releases_database_file_after_each_operation(tmp_path) -> None:
    """Windows must be able to delete a DB immediately after store calls return."""

    path = tmp_path / "released.db"
    store = EventStore(path)
    session = store.create_session("release probe")
    store.list_sessions()
    store.load_history(session.id)

    path.unlink()
    assert not path.exists()
