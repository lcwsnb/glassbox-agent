from __future__ import annotations

import copy
from collections.abc import Callable

from glassbox.domain import MemoryCapsule, ModelDecision


class ScriptedProvider:
    model = "scripted-test-model"

    def __init__(
        self,
        decisions: list[ModelDecision],
        *,
        summary: MemoryCapsule | None = None,
        summary_error: Exception | None = None,
        retry_error: Exception | None = None,
    ) -> None:
        self.decisions = list(decisions)
        self.summary_value = summary or MemoryCapsule(facts=["compressed fact"])
        self.summary_error = summary_error
        self.retry_error = retry_error
        self.complete_calls = 0
        self.summarize_calls = 0
        self.seen_messages: list[list[dict]] = []
        self.seen_tools: list[list[dict]] = []

    def complete(
        self,
        messages: list[dict],
        _tools: list[dict],
        on_retry: Callable | None = None,
    ) -> ModelDecision:
        self.complete_calls += 1
        self.seen_messages.append(copy.deepcopy(messages))
        self.seen_tools.append(copy.deepcopy(_tools))
        if self.retry_error is not None and on_retry is not None:
            on_retry(1, self.retry_error)
            self.retry_error = None
        if not self.decisions:
            raise AssertionError("ScriptedProvider ran out of decisions")
        return self.decisions.pop(0)

    def summarize(self, _memory: MemoryCapsule, _messages: list[dict]) -> MemoryCapsule:
        self.summarize_calls += 1
        if self.summary_error is not None:
            raise self.summary_error
        return self.summary_value.model_copy(deep=True)

    def doctor(self) -> dict:
        return {"ok": True, "model": self.model}
