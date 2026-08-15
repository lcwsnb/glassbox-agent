from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from glassbox.domain import MemoryCapsule
from glassbox.provider import DeepSeekProvider, ProviderError


def response(*, content=None, tool_calls=None, usage=None, reasoning_content=None):
    message = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
        reasoning_content=reasoning_content,
        model_extra={},
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=usage or {"prompt_tokens": 3, "completion_tokens": 2},
    )


def tool_call(call_id="c1", name="calculator", arguments='{"expression":"2+2"}'):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


class FakeCompletions:
    def __init__(self, values):
        self.values = list(values)
        self.kwargs = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class FakeClient:
    def __init__(self, values):
        self.completions = FakeCompletions(values)
        self.chat = SimpleNamespace(completions=self.completions)


def provider(values, **kwargs) -> DeepSeekProvider:
    return DeepSeekProvider(client=FakeClient(values), sleep=lambda _seconds: None, **kwargs)


def test_parse_direct_and_tool_decisions() -> None:
    model = provider([response(content="hello", reasoning_content="private scratchpad")])
    direct = model.complete([], [])
    assert direct.kind == "final" and direct.content == "hello"
    assert direct.reasoning_content == "private scratchpad"
    assert model.client.chat.completions.kwargs[0]["extra_body"] == {
        "thinking": {"type": "disabled"}
    }
    assert "tools" not in model.client.chat.completions.kwargs[0]
    assert "tool_choice" not in model.client.chat.completions.kwargs[0]
    schema = [{"type": "function", "function": {"name": "calculator"}}]
    tool_model = provider([response(tool_calls=[tool_call()])])
    tools = tool_model.complete([], schema)
    assert tools.kind == "tool_calls"
    assert tools.tool_calls[0].arguments == {"expression": "2+2"}
    assert tools.usage["prompt_tokens"] == 3
    assert tool_model.client.chat.completions.kwargs[0]["tools"] == schema
    assert tool_model.client.chat.completions.kwargs[0]["tool_choice"] == "auto"


def test_invalid_tool_json_is_preserved_as_parse_error() -> None:
    decision = provider([response(tool_calls=[tool_call(arguments="not-json")])]).complete([], [])
    assert decision.tool_calls[0].parse_error
    assert decision.tool_calls[0].raw_arguments == "not-json"


def test_retryable_failure_notifies_observer_then_succeeds() -> None:
    error = RuntimeError("timeout")
    retries = []
    client = FakeClient([error, response(content="ok")])
    model = DeepSeekProvider(client=client, sleep=lambda _seconds: None, max_retries=2)
    result = model.complete([], [], on_retry=lambda attempt, exc: retries.append((attempt, exc)))
    assert result.content == "ok"
    assert retries == [(1, error)]


def test_non_retryable_and_empty_response_are_actionable() -> None:
    error = RuntimeError("invalid request")
    with pytest.raises(ProviderError, match="Check the API key"):
        provider([error]).complete([], [])
    with pytest.raises(ProviderError, match="no choices"):
        provider([SimpleNamespace(choices=[])]).complete([], [])


def test_http_retry_classification() -> None:
    class APIError(RuntimeError):
        def __init__(self, status_code: int) -> None:
            super().__init__(f"HTTP {status_code}")
            self.status_code = status_code

    retries = []
    model = provider([APIError(429), response(content="ok")])
    assert (
        model.complete([], [], on_retry=lambda attempt, _exc: retries.append(attempt)).content
        == "ok"
    )
    assert retries == [1]
    rejected = provider([APIError(401)])
    with pytest.raises(ProviderError, match="HTTP 401"):
        rejected.complete([], [], on_retry=lambda attempt, _exc: retries.append(attempt))
    assert retries == [1]


def test_summary_parsing_and_invalid_json() -> None:
    value = json.dumps({"goals": ["ship"], "facts": [], "tool_facts": [], "open_items": []})
    result = provider([response(content=value)]).summarize(MemoryCapsule(), [])
    assert result.goals == ["ship"]
    with pytest.raises(ProviderError, match="invalid JSON"):
        provider([response(content="bad")]).summarize(MemoryCapsule(), [])


def test_doctor_validates_tool_name_arguments_and_id() -> None:
    result = provider(
        [
            response(
                tool_calls=[
                    tool_call(
                        call_id="probe", name="doctor_echo", arguments='{"value":"GLASSBOX_OK"}'
                    )
                ]
            )
        ]
    ).doctor()
    assert result["ok"] and result["tool_call_id"] == "probe"
    with pytest.raises(ProviderError, match="exactly one"):
        provider([response(tool_calls=[])]).doctor()
    with pytest.raises(ProviderError, match="wrong name"):
        provider(
            [response(tool_calls=[tool_call(name="wrong", arguments='{"value":"x"}')])]
        ).doctor()
    with pytest.raises(ProviderError, match="invalid argument JSON"):
        provider([response(tool_calls=[tool_call(name="doctor_echo", arguments="bad")])]).doctor()


def test_missing_key_fails_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="DEEPSEEK_API_KEY"):
        DeepSeekProvider(api_key="")
