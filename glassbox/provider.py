"""DeepSeek Chat Completions adapter.

Only public assistant text, normalized tool calls, and usage are returned to the
runtime. Optional ``reasoning_content`` is transport-only and excluded from model
serialization so the event ledger cannot persist it accidentally.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from typing import Any, Protocol

from openai import OpenAI

from .domain import MemoryCapsule, ModelDecision, ToolCall


class ProviderError(RuntimeError):
    """A user-facing provider error with actionable context."""


class LLMProvider(Protocol):
    model: str

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_retry: Callable[[int, Exception], None] | None = None,
    ) -> ModelDecision: ...

    def summarize(self, memory: MemoryCapsule, messages: list[dict[str, Any]]) -> MemoryCapsule: ...

    def doctor(self) -> dict[str, Any]: ...


def _attribute(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


class DeepSeekProvider:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 45.0,
        max_retries: int = 2,
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        self.base_url = base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._sleep = sleep
        if client is None:
            if not self.api_key:
                raise ProviderError(
                    "DEEPSEEK_API_KEY is missing. Copy .env.example to .env, add the key, "
                    "then run `glassbox doctor`."
                )
            client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                max_retries=0,
            )
        self.client = client

    def _is_retryable(self, error: Exception) -> bool:
        status_code = _attribute(error, "status_code")
        if status_code == 429 or (isinstance(status_code, int) and status_code >= 500):
            return True
        fingerprint = f"{type(error).__name__} {error}".casefold()
        retry_markers = ("timeout", "timed out", "connection", "ratelimit", "internalserver")
        return any(marker in fingerprint for marker in retry_markers)

    def _request(
        self,
        *,
        on_retry: Callable[[int, Exception], None] | None = None,
        **kwargs: Any,
    ) -> Any:
        for attempt in range(self.max_retries + 1):
            try:
                return self.client.chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 - SDK exceptions vary by version/provider
                if attempt >= self.max_retries or not self._is_retryable(exc):
                    status = _attribute(exc, "status_code")
                    detail = f" HTTP {status}." if status else ""
                    raise ProviderError(
                        f"DeepSeek request failed.{detail} Cause: {exc}. "
                        "Check the API key, model name, network, and account quota."
                    ) from exc
                retry_number = attempt + 1
                if on_retry is not None:
                    on_retry(retry_number, exc)
                self._sleep(min(0.25 * (2**attempt), 2.0))
        raise AssertionError("unreachable")

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_retry: Callable[[int, Exception], None] | None = None,
    ) -> ModelDecision:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
        if tools:
            request["tools"] = tools
            request["tool_choice"] = "auto"
        response = self._request(on_retry=on_retry, **request)
        choices = _attribute(response, "choices", [])
        if not choices:
            raise ProviderError("DeepSeek returned no choices. Retry or choose another model.")
        message = _attribute(choices[0], "message")
        if message is None:
            raise ProviderError("DeepSeek returned a choice without a message.")
        calls: list[ToolCall] = []
        for raw_call in _attribute(message, "tool_calls", None) or []:
            function = _attribute(raw_call, "function", {})
            raw_arguments = _attribute(function, "arguments", "{}") or "{}"
            parse_error = None
            try:
                arguments = json.loads(raw_arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must decode to a JSON object")
            except (json.JSONDecodeError, ValueError) as exc:
                arguments = {}
                parse_error = str(exc)
            calls.append(
                ToolCall(
                    id=str(_attribute(raw_call, "id", "")),
                    name=str(_attribute(function, "name", "")),
                    arguments=arguments,
                    raw_arguments=raw_arguments,
                    parse_error=parse_error,
                )
            )
        usage_value = _attribute(response, "usage", {}) or {}
        if hasattr(usage_value, "model_dump"):
            usage_value = usage_value.model_dump()
        usage = {
            key: int(value) for key, value in dict(usage_value).items() if isinstance(value, int)
        }
        content = _attribute(message, "content")
        reasoning_content = _attribute(message, "reasoning_content")
        if reasoning_content is None:
            reasoning_content = (_attribute(message, "model_extra", {}) or {}).get(
                "reasoning_content"
            )
        return ModelDecision(
            kind="tool_calls" if calls else "final",
            content=content,
            tool_calls=calls,
            usage=usage,
            reasoning_content=reasoning_content,
        )

    def summarize(self, memory: MemoryCapsule, messages: list[dict[str, Any]]) -> MemoryCapsule:
        prompt = (
            "Create a compact memory capsule from the previous capsule and conversation. "
            "Return one JSON object with exactly these array fields: goals, facts, tool_facts, "
            "open_items. Preserve names, numbers, decisions, tool-derived facts, and unfinished "
            "work. Do not include hidden reasoning.\n\n"
            f"PREVIOUS_CAPSULE:\n{memory.model_dump_json(exclude={'through_event_id'})}\n\n"
            f"MESSAGES:\n{json.dumps(messages, ensure_ascii=False)}"
        )
        response = self._request(
            model=self.model,
            messages=[
                {"role": "system", "content": "You compress conversation memory into JSON."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            extra_body={"thinking": {"type": "disabled"}},
        )
        message = _attribute(_attribute(response, "choices", [None])[0], "message")
        content = _attribute(message, "content", "")
        try:
            return MemoryCapsule.model_validate_json(content)
        except Exception as exc:  # noqa: BLE001 - convert parser details to provider boundary
            raise ProviderError(
                f"Context compaction returned invalid JSON: {exc}. Falling back to a safe window."
            ) from exc

    def doctor(self) -> dict[str, Any]:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "doctor_echo",
                    "description": "Echo a probe value to verify function calling.",
                    "parameters": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                },
            }
        ]
        response = self._request(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": "Call doctor_echo exactly once with value GLASSBOX_OK.",
                }
            ],
            tools=tools,
            tool_choice={"type": "function", "function": {"name": "doctor_echo"}},
            temperature=0,
            extra_body={"thinking": {"type": "disabled"}},
        )
        message = _attribute(_attribute(response, "choices", [None])[0], "message")
        calls = _attribute(message, "tool_calls", None) or []
        if len(calls) != 1:
            raise ProviderError(
                "The endpoint answered, but did not return exactly one tool call. "
                "Use a DeepSeek model/endpoint with function-calling support."
            )
        call = calls[0]
        function = _attribute(call, "function", {})
        try:
            arguments = json.loads(_attribute(function, "arguments", "{}"))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("Tool-call probe returned invalid argument JSON.") from exc
        call_id = _attribute(call, "id")
        if _attribute(function, "name") != "doctor_echo" or arguments.get("value") != "GLASSBOX_OK":
            raise ProviderError("Tool-call probe returned the wrong name or arguments.")
        if not call_id:
            raise ProviderError("Tool-call probe omitted tool_call_id.")
        return {
            "ok": True,
            "model": self.model,
            "base_url": self.base_url,
            "tool_call_id": call_id,
        }
