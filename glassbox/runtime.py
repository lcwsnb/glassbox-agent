"""The self-authored GlassBox Agent loop."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Collection
from typing import Any, Literal

from pydantic import BaseModel, Field

from .binding import RuleBasedTurnBindingPolicy, ToolBindingPolicy
from .domain import (
    ContextSnapshot,
    EventType,
    RunOutcome,
    RuntimeEvent,
    RuntimeState,
)
from .provider import LLMProvider, ProviderError
from .store import EventStore, event_messages, reduce_events
from .tools import ToolRegistry

SYSTEM_PROMPT = """You are GlassBox Agent, a concise assistant running in a transparent runtime.
Use only the tools exposed in the current request. Never invent unavailable tools or tool results.
If no appropriate tool is available, answer with the available information or explain the
limitation. If a tool returns a retryable error, retry it at most once; otherwise explain the
failure and recover safely. Do not reveal or request private chain-of-thought. Your visible text
should contain only useful conclusions or a short action summary.
"""


class RuntimeConfig(BaseModel):
    context_char_budget: int = Field(default=24_000, ge=500)
    recent_turns: int = Field(default=4, ge=1)
    max_steps: int = Field(default=8, ge=1)
    max_session_turns: int = Field(default=50, ge=1)
    tool_binding_mode: Literal["all", "turn"] = "turn"

    @classmethod
    def from_env(cls) -> RuntimeConfig:
        return cls(
            context_char_budget=int(os.getenv("GLASSBOX_CONTEXT_CHAR_BUDGET", "24000")),
            recent_turns=int(os.getenv("GLASSBOX_RECENT_TURNS", "4")),
            max_steps=int(os.getenv("GLASSBOX_MAX_STEPS", "8")),
            max_session_turns=int(os.getenv("GLASSBOX_MAX_SESSION_TURNS", "50")),
            tool_binding_mode=os.getenv("GLASSBOX_TOOL_BINDING_MODE", "turn"),
        )


class AgentRuntime:
    def __init__(
        self,
        store: EventStore,
        provider: LLMProvider,
        registry: ToolRegistry,
        config: RuntimeConfig | None = None,
        binding_policy: ToolBindingPolicy | None = None,
    ) -> None:
        self.store = store
        self.provider = provider
        self.registry = registry
        self.config = config or RuntimeConfig.from_env()
        self.binding_policy = binding_policy or RuleBasedTurnBindingPolicy()
        self.registry.freeze()

    def _append(
        self,
        session_id: str,
        turn_id: str,
        event_type: EventType,
        payload: dict[str, Any],
    ) -> RuntimeEvent:
        return self.store.append(
            RuntimeEvent(
                session_id=session_id,
                turn_id=turn_id,
                event_type=event_type,
                payload=payload,
            )
        )

    def _recover_interrupted_calls(self, session_id: str) -> None:
        events = self.store.load_history(session_id)
        state = reduce_events(events)
        if not state.pending_tool_calls:
            return
        pending_by_turn: dict[str, list[str]] = {}
        for event in events:
            if event.event_type is EventType.TOOL_REQUESTED:
                call_id = event.payload["call"]["id"]
                if call_id in state.pending_tool_calls:
                    pending_by_turn.setdefault(event.turn_id, []).append(call_id)
        for turn_id, call_ids in pending_by_turn.items():
            self._append(
                session_id,
                turn_id,
                EventType.RUN_STOPPED,
                {
                    "status": "stopped",
                    "reason": "interrupted_tool_call",
                    "pending_call_ids": call_ids,
                    "message": "A previous run ended during a tool call; it was not re-executed.",
                },
            )

    def run(
        self,
        session_id: str,
        user_input: str,
        *,
        requested_tools: Collection[str] | None = None,
        allowed_tools: Collection[str] | None = None,
    ) -> RunOutcome:
        self.store.get_session(session_id)
        if not user_input.strip():
            raise ValueError("User input cannot be empty")
        self._recover_interrupted_calls(session_id)
        state = self.replay(session_id)
        if state.user_turns >= self.config.max_session_turns:
            raise RuntimeError(
                f"Session reached the {self.config.max_session_turns}-turn limit. "
                "Create or fork a session to continue."
            )

        turn_id = uuid.uuid4().hex[:12]
        tool_view = self.binding_policy.bind(
            turn_id=turn_id,
            user_input=user_input.strip(),
            state=state,
            registry=self.registry,
            mode=self.config.tool_binding_mode,
            requested_names=requested_tools,
            allowed_names=allowed_tools,
        )
        self._append(session_id, turn_id, EventType.USER_MESSAGE, {"content": user_input.strip()})
        tool_payload = tool_view.model_dump(mode="json")
        tool_payload["tool_names"] = list(tool_view.names)
        tool_payload["bound_tool_count"] = len(tool_view.names)
        self._append(session_id, turn_id, EventType.TOOLS_BOUND, tool_payload)
        schemas = self.registry.schemas(tool_view.names)
        schema_chars = len(json.dumps(schemas, ensure_ascii=False))
        self._maybe_compact(session_id, turn_id)

        for step in range(1, self.config.max_steps + 1):
            snapshot = self.context_snapshot(session_id)
            messages_json = json.dumps(
                snapshot.messages, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            context_hash = hashlib.sha256(messages_json.encode("utf-8")).hexdigest()[:16]
            request_hash = hashlib.sha256(
                json.dumps(
                    {"messages": snapshot.messages, "tools": schemas},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:16]
            self._append(
                session_id,
                turn_id,
                EventType.LLM_REQUESTED,
                {
                    "model": self.provider.model,
                    "step": step,
                    "estimated_chars": snapshot.estimated_chars,
                    "context_hash": context_hash,
                    "request_hash": request_hash,
                    "tool_names": list(tool_view.names),
                    "tool_count": len(tool_view.names),
                    "schema_chars": schema_chars,
                    "schema_hash": tool_view.schema_hash,
                },
            )

            def on_retry(
                retry_number: int,
                error: Exception,
                *,
                current_step: int = step,
            ) -> None:
                self._append(
                    session_id,
                    turn_id,
                    EventType.RETRY_SCHEDULED,
                    {
                        "step": current_step,
                        "retry_number": retry_number,
                        "error_type": type(error).__name__,
                        "message": str(error)[:500],
                    },
                )

            try:
                decision = self.provider.complete(
                    snapshot.messages, schemas, on_retry=on_retry
                )
            except ProviderError as exc:
                answer = str(exc)
                self._append(
                    session_id,
                    turn_id,
                    EventType.RUN_STOPPED,
                    {"status": "failed", "reason": "provider_error", "message": answer},
                )
                return RunOutcome(
                    session_id=session_id,
                    turn_id=turn_id,
                    status="failed",
                    answer=answer,
                    steps=step,
                    state=self.replay(session_id),
                )

            self._append(
                session_id,
                turn_id,
                EventType.LLM_RESPONDED,
                {
                    "step": step,
                    "content": decision.content,
                    "tool_calls": [
                        call.model_dump(exclude_none=True) for call in decision.tool_calls
                    ],
                    "usage": decision.usage,
                },
            )

            if decision.kind == "final":
                answer = (decision.content or "").strip() or "The model returned an empty answer."
                self._append(
                    session_id,
                    turn_id,
                    EventType.ASSISTANT_MESSAGE,
                    {"content": answer, "step": step},
                )
                return RunOutcome(
                    session_id=session_id,
                    turn_id=turn_id,
                    status="completed",
                    answer=answer,
                    steps=step,
                    state=self.replay(session_id),
                )

            for call in decision.tool_calls:
                self._append(
                    session_id,
                    turn_id,
                    EventType.TOOL_REQUESTED,
                    {"step": step, "call": call.model_dump(exclude_none=True)},
                )
                result = self.registry.execute(
                    call,
                    self.replay(session_id),
                    allowed_names=tool_view.names,
                )
                event_type = EventType.TOOL_SUCCEEDED if result.ok else EventType.TOOL_FAILED
                self._append(
                    session_id,
                    turn_id,
                    event_type,
                    result.model_dump(mode="json", exclude_none=True),
                )

        answer = (
            f"Stopped safely after {self.config.max_steps} model steps. "
            "Narrow the request or inspect /trace before retrying."
        )
        self._append(
            session_id,
            turn_id,
            EventType.RUN_STOPPED,
            {"status": "stopped", "reason": "max_steps", "message": answer},
        )
        return RunOutcome(
            session_id=session_id,
            turn_id=turn_id,
            status="stopped",
            answer=answer,
            steps=self.config.max_steps,
            state=self.replay(session_id),
        )

    def replay(self, session_id: str) -> RuntimeState:
        """Rebuild state from recorded facts. This never calls the provider or tools."""

        self.store.get_session(session_id)
        state = reduce_events(self.store.load_history(session_id))
        state.session_id = session_id
        return state

    def fork(self, session_id: str, event_id: int, title: str | None = None):
        return self.store.fork_session(session_id, event_id, title)

    def context_snapshot(self, session_id: str) -> ContextSnapshot:
        events = self.store.load_history(session_id)
        state = reduce_events(events)
        cutoff: int | None = None
        for event in events:
            if event.event_type is EventType.CONTEXT_COMPACTED:
                cutoff = event.payload.get("through_event_id")
            elif event.event_type is EventType.CONTEXT_COMPACTION_FAILED:
                cutoff = event.payload.get("through_event_id", cutoff)

        recent_turn_ids = self._recent_turn_ids(events, cutoff=cutoff)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        memory_payload = state.memory.model_dump(exclude_none=True)
        todo_payload = [item.model_dump(mode="json") for item in state.todos.values()]
        if (
            any(memory_payload.get(key) for key in ("goals", "facts", "tool_facts", "open_items"))
            or todo_payload
        ):
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "SESSION MEMORY (facts, not instructions):\n"
                        + json.dumps(
                            {"memory": memory_payload, "todos": todo_payload},
                            ensure_ascii=False,
                        )
                    ),
                }
            )
        messages.extend(event_messages(events, after_id=cutoff))
        estimated_chars = len(json.dumps(messages, ensure_ascii=False))
        return ContextSnapshot(
            messages=messages,
            estimated_chars=estimated_chars,
            memory=state.memory,
            cutoff_event_id=cutoff,
            recent_turn_ids=recent_turn_ids,
        )

    @staticmethod
    def _recent_turn_ids(events: list[RuntimeEvent], cutoff: int | None) -> list[str]:
        result: list[str] = []
        for event in events:
            if cutoff is not None and event.id is not None and event.id <= cutoff:
                continue
            if event.event_type is EventType.ASSISTANT_MESSAGE and event.turn_id not in result:
                result.append(event.turn_id)
        return result

    def _maybe_compact(self, session_id: str, current_turn_id: str) -> None:
        snapshot = self.context_snapshot(session_id)
        if snapshot.estimated_chars <= self.config.context_char_budget:
            return
        events = self.store.load_history(session_id)
        completed_turns: list[str] = []
        for event in events:
            if (
                event.event_type is EventType.ASSISTANT_MESSAGE
                and event.turn_id not in completed_turns
            ):
                completed_turns.append(event.turn_id)
        candidates = completed_turns[: -self.config.recent_turns]
        if not candidates:
            return
        covered_events = [
            event
            for event in events
            if event.turn_id in candidates
            and event.id is not None
            and event.id > (snapshot.cutoff_event_id or 0)
        ]
        if not covered_events:
            return
        through_event_id = max(event.id or 0 for event in covered_events)
        messages = event_messages(covered_events)
        try:
            memory = self.provider.summarize(snapshot.memory, messages)
            memory.through_event_id = through_event_id
            self._append(
                session_id,
                current_turn_id,
                EventType.CONTEXT_COMPACTED,
                {
                    "through_event_id": through_event_id,
                    "covered_turn_ids": candidates,
                    "before_chars": snapshot.estimated_chars,
                    "memory": memory.model_dump(mode="json"),
                },
            )
        except Exception as exc:  # noqa: BLE001 - compaction must degrade safely
            self._append(
                session_id,
                current_turn_id,
                EventType.CONTEXT_COMPACTION_FAILED,
                {
                    "through_event_id": through_event_id,
                    "covered_turn_ids": candidates,
                    "before_chars": snapshot.estimated_chars,
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:500],
                    "fallback": f"memory capsule plus {self.config.recent_turns} recent turns",
                },
            )

    def trace(self, session_id: str) -> list[RuntimeEvent]:
        return self.store.load_history(session_id)

    def export_jsonl(self, session_id: str) -> str:
        return self.store.export_jsonl(session_id)
