from __future__ import annotations

import json

import pytest
from conftest import ScriptedProvider

from glassbox.domain import EventType, MemoryCapsule, ModelDecision, RuntimeEvent, ToolCall
from glassbox.runtime import AgentRuntime, RuntimeConfig
from glassbox.store import EventStore
from glassbox.tools import build_default_registry


def runtime(tmp_path, provider, **config) -> AgentRuntime:
    return AgentRuntime(
        EventStore(tmp_path / "runtime.db"),
        provider,
        build_default_registry(),
        RuntimeConfig(**config),
    )


def test_direct_answer_and_offline_replay(tmp_path) -> None:
    provider = ScriptedProvider([ModelDecision(kind="final", content="hello")])
    agent = runtime(tmp_path, provider)
    session = agent.store.create_session()
    outcome = agent.run(session.id, "hi")
    assert outcome.status == "completed"
    assert outcome.answer == "hello"
    before = provider.complete_calls
    replayed = agent.replay(session.id)
    assert replayed == outcome.state
    assert provider.complete_calls == before
    assert agent.context_snapshot(session.id).messages[-1]["content"] == "hello"


def test_reasoning_and_api_key_never_enter_event_ledger(tmp_path) -> None:
    secret = "test-api-key-never-persist-this"
    decision = ModelDecision(
        kind="final",
        content="public answer",
        reasoning_content="private chain of thought",
    )
    provider = ScriptedProvider([decision])
    provider.api_key = secret
    agent = runtime(tmp_path, provider)
    session = agent.store.create_session()
    agent.run(session.id, "hello")
    exported = agent.export_jsonl(session.id)
    assert secret not in exported
    assert "private chain of thought" not in exported
    assert "reasoning_content" not in exported


def test_multi_step_tool_chain_and_todo_projection(tmp_path) -> None:
    decisions = [
        ModelDecision(
            kind="tool_calls",
            tool_calls=[ToolCall(id="s", name="search_docs", arguments={"query": "上海餐补"})],
        ),
        ModelDecision(
            kind="tool_calls",
            tool_calls=[ToolCall(id="r", name="read_doc", arguments={"doc_id": "travel-domestic"})],
        ),
        ModelDecision(
            kind="tool_calls",
            tool_calls=[ToolCall(id="c", name="calculator", arguments={"expression": "120*3"})],
        ),
        ModelDecision(
            kind="tool_calls",
            tool_calls=[
                ToolCall(id="t", name="todo", arguments={"action": "add", "title": "周五提交报销"})
            ],
        ),
        ModelDecision(kind="final", content="餐补共360元，待办已添加。"),
    ]
    agent = runtime(tmp_path, ScriptedProvider(decisions))
    session = agent.store.create_session()
    outcome = agent.run(session.id, "安排报销")
    assert outcome.steps == 5
    assert "360" in outcome.answer
    assert next(iter(outcome.state.todos.values())).title == "周五提交报销"
    event_types = [event.event_type for event in agent.trace(session.id)]
    assert event_types.count(EventType.TOOL_SUCCEEDED) == 4


def test_tool_failure_is_returned_to_model_then_recovered(tmp_path) -> None:
    decisions = [
        ModelDecision(
            kind="tool_calls",
            tool_calls=[ToolCall(id="bad", name="calculator", arguments={"expression": "1/0"})],
        ),
        ModelDecision(kind="final", content="计算失败，已安全停止。"),
    ]
    provider = ScriptedProvider(decisions, retry_error=TimeoutError("temporary"))
    agent = runtime(tmp_path, provider)
    session = agent.store.create_session()
    outcome = agent.run(session.id, "calculate")
    events = agent.trace(session.id)
    assert outcome.status == "completed"
    assert any(event.event_type is EventType.TOOL_FAILED for event in events)
    assert any(event.event_type is EventType.RETRY_SCHEDULED for event in events)
    second_request = provider.seen_messages[1]
    assert any(message.get("role") == "tool" for message in second_request)


def test_max_steps_and_session_turn_limit(tmp_path) -> None:
    decisions = [
        ModelDecision(
            kind="tool_calls",
            tool_calls=[ToolCall(id=f"c{i}", name="calculator", arguments={"expression": "1+1"})],
        )
        for i in range(2)
    ]
    agent = runtime(tmp_path, ScriptedProvider(decisions), max_steps=2, max_session_turns=1)
    session = agent.store.create_session()
    outcome = agent.run(session.id, "loop")
    assert outcome.status == "stopped"
    assert "2 model steps" in outcome.answer
    with pytest.raises(RuntimeError, match="turn limit"):
        agent.run(session.id, "again")


def test_provider_failure_becomes_failed_outcome(tmp_path) -> None:
    from glassbox.provider import ProviderError

    class BrokenProvider(ScriptedProvider):
        def complete(self, *_args, **_kwargs):
            raise ProviderError("quota gone")

    agent = runtime(tmp_path, BrokenProvider([]))
    session = agent.store.create_session()
    outcome = agent.run(session.id, "hello")
    assert outcome.status == "failed"
    assert "quota" in outcome.answer


def test_successful_compaction_preserves_memory_and_recent_turn(tmp_path) -> None:
    provider = ScriptedProvider(
        [
            ModelDecision(kind="final", content="a"),
            ModelDecision(kind="final", content="b"),
            ModelDecision(kind="final", content="c"),
        ],
        summary=MemoryCapsule(goals=["remember me"], facts=["120 per day"]),
    )
    agent = runtime(
        tmp_path,
        provider,
        context_char_budget=500,
        recent_turns=1,
        max_steps=2,
    )
    session = agent.store.create_session()
    agent.run(session.id, "x" * 400)
    agent.run(session.id, "y" * 400)
    agent.run(session.id, "z" * 400)
    events = agent.trace(session.id)
    compacted = [e for e in events if e.event_type is EventType.CONTEXT_COMPACTED]
    assert compacted
    assert provider.summarize_calls >= 1
    assert agent.replay(session.id).memory.facts == ["120 per day"]
    snapshot = agent.context_snapshot(session.id)
    assert snapshot.cutoff_event_id is not None
    assert "remember me" in json.dumps(snapshot.messages, ensure_ascii=False)


def test_failed_compaction_uses_cutoff_fallback(tmp_path) -> None:
    provider = ScriptedProvider(
        [
            ModelDecision(kind="final", content="a"),
            ModelDecision(kind="final", content="b"),
            ModelDecision(kind="final", content="c"),
        ],
        summary_error=RuntimeError("bad summary"),
    )
    agent = runtime(
        tmp_path,
        provider,
        context_char_budget=500,
        recent_turns=1,
        max_steps=2,
    )
    session = agent.store.create_session()
    agent.run(session.id, "x" * 400)
    agent.run(session.id, "y" * 400)
    agent.run(session.id, "z" * 400)
    assert any(
        event.event_type is EventType.CONTEXT_COMPACTION_FAILED for event in agent.trace(session.id)
    )
    assert agent.context_snapshot(session.id).cutoff_event_id is not None


def test_interrupted_tool_call_is_marked_without_reexecution(tmp_path) -> None:
    provider = ScriptedProvider([ModelDecision(kind="final", content="recovered")])
    agent = runtime(tmp_path, provider)
    session = agent.store.create_session()
    turn = "old"
    agent.store.append(
        RuntimeEvent(
            session_id=session.id,
            turn_id=turn,
            event_type=EventType.USER_MESSAGE,
            payload={"content": "old request"},
        )
    )
    call_data = {"id": "dangling", "name": "calculator", "arguments": {"expression": "2+2"}}
    agent.store.append(
        RuntimeEvent(
            session_id=session.id,
            turn_id=turn,
            event_type=EventType.LLM_RESPONDED,
            payload={"content": None, "tool_calls": [call_data]},
        )
    )
    agent.store.append(
        RuntimeEvent(
            session_id=session.id,
            turn_id=turn,
            event_type=EventType.TOOL_REQUESTED,
            payload={"call": call_data},
        )
    )
    outcome = agent.run(session.id, "continue")
    assert outcome.status == "completed"
    assert not outcome.state.pending_tool_calls
    stopped = [e for e in agent.trace(session.id) if e.event_type is EventType.RUN_STOPPED]
    assert stopped[0].payload["reason"] == "interrupted_tool_call"


def test_fork_runtime_inherits_state_and_isolates_future(tmp_path) -> None:
    provider = ScriptedProvider(
        [
            ModelDecision(kind="final", content="parent"),
            ModelDecision(kind="final", content="child"),
        ]
    )
    agent = runtime(tmp_path, provider)
    parent = agent.store.create_session("parent")
    first = agent.run(parent.id, "one")
    checkpoint = next(
        event.id
        for event in agent.trace(parent.id)
        if event.event_type is EventType.ASSISTANT_MESSAGE
    )
    child = agent.fork(parent.id, checkpoint or 0)
    agent.run(child.id, "two")
    assert agent.replay(parent.id).last_assistant_message == "parent"
    assert agent.replay(child.id).last_assistant_message == "child"
    assert first.state.user_turns == 1


def test_independent_sessions_isolate_messages_and_todos(tmp_path) -> None:
    provider = ScriptedProvider(
        [
            ModelDecision(
                kind="tool_calls",
                tool_calls=[
                    ToolCall(id="a", name="todo", arguments={"action": "add", "title": "A only"})
                ],
            ),
            ModelDecision(kind="final", content="A done"),
            ModelDecision(
                kind="tool_calls",
                tool_calls=[
                    ToolCall(id="b", name="todo", arguments={"action": "add", "title": "B only"})
                ],
            ),
            ModelDecision(kind="final", content="B done"),
        ]
    )
    agent = runtime(tmp_path, provider)
    session_a = agent.store.create_session("A")
    session_b = agent.store.create_session("B")
    agent.run(session_a.id, "make A")
    agent.run(session_b.id, "make B")
    state_a = agent.replay(session_a.id)
    state_b = agent.replay(session_b.id)
    assert {item.title for item in state_a.todos.values()} == {"A only"}
    assert {item.title for item in state_b.todos.values()} == {"B only"}
    assert "make B" not in json.dumps(state_a.messages)
    assert "make A" not in json.dumps(state_b.messages)
