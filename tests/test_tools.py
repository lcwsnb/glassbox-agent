from __future__ import annotations

import json
import time

import pytest
from pydantic import BaseModel

from glassbox.domain import RuntimeState, ToolCall
from glassbox.tools import (
    CalculatorArgs,
    ToolRegistry,
    ToolSpec,
    build_default_registry,
    calculator,
)


def call(name: str, **arguments) -> ToolCall:
    return ToolCall(id=f"call-{name}", name=name, arguments=arguments)


def execute(
    registry: ToolRegistry,
    tool_call: ToolCall,
    state: RuntimeState | None = None,
    *,
    allowed_names=None,
):
    return registry.execute(
        tool_call,
        state or RuntimeState(),
        allowed_names=registry.names() if allowed_names is None else allowed_names,
    )


def test_default_registry_exposes_four_json_schemas() -> None:
    schemas = build_default_registry().schemas()
    assert {item["function"]["name"] for item in schemas} == {
        "calculator",
        "search_docs",
        "read_doc",
        "todo",
    }
    assert all(item["function"]["parameters"]["type"] == "object" for item in schemas)


def test_registry_rejects_duplicate_and_invalid_names() -> None:
    registry = ToolRegistry()
    spec = ToolSpec("ok", "ok", CalculatorArgs, calculator)
    registry.register(spec)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(spec)
    with pytest.raises(ValueError, match="Invalid tool name"):
        registry.register(ToolSpec("not valid!", "bad", CalculatorArgs, calculator))
    registry.freeze()
    with pytest.raises(RuntimeError, match="frozen"):
        registry.register(ToolSpec("later", "late", CalculatorArgs, calculator))


def test_registry_projects_subset_and_rejects_unknown_schema() -> None:
    registry = build_default_registry()
    schemas = registry.schemas(("todo", "calculator", "todo"))
    assert [item["function"]["name"] for item in schemas] == ["todo", "calculator"]
    assert registry.schema_hash(("todo",)) == registry.schema_hash(("todo",))
    assert registry.catalog_hash()
    with pytest.raises(KeyError, match="Unknown tools"):
        registry.schemas(("missing",))
    with pytest.raises(KeyError, match="Unknown tool"):
        registry.get("missing")


@pytest.mark.parametrize(
    ("expression", "expected"),
    [("2 + 3 * 4", 14), ("9 / 3", 3), ("-5 + 2", -3), ("7 % 4", 3)],
)
def test_calculator_evaluates_safe_arithmetic(expression: str, expected: int) -> None:
    registry = build_default_registry()
    result = execute(registry, call("calculator", expression=expression))
    assert result.ok
    assert result.data["result"] == expected


@pytest.mark.parametrize(
    "expression",
    ["__import__('os').system('echo bad')", "(1).__class__", "2 ** 99", "1 / 0"],
)
def test_calculator_rejects_unsafe_or_invalid_expressions(expression: str) -> None:
    registry = build_default_registry()
    result = execute(registry, call("calculator", expression=expression))
    assert not result.ok
    assert result.error_code in {"UNSAFE_EXPRESSION", "CALCULATION_ERROR"}


def test_search_and_read_docs() -> None:
    registry = build_default_registry()
    search = execute(registry, call("search_docs", query="上海出差餐补", limit=2))
    assert search.ok
    assert search.data["results"][0]["doc_id"] == "travel-domestic"
    read = execute(registry, call("read_doc", doc_id="travel-domestic"))
    assert read.ok
    assert "120元" in read.content
    missing = execute(registry, call("read_doc", doc_id="missing"))
    assert missing.error_code == "DOC_NOT_FOUND"


def test_todo_mutations_are_returned_as_event_data() -> None:
    registry = build_default_registry()
    add = execute(registry, call("todo", action="add", title="提交报销"))
    assert add.ok
    item = add.data["todo_mutation"]["item"]
    state = RuntimeState(todos={item["id"]: item})
    listed = execute(registry, call("todo", action="list"), state)
    assert json.loads(listed.content)["items"][0]["title"] == "提交报销"
    completed = execute(registry, call("todo", action="complete", item_id=item["id"]), state)
    assert completed.data["todo_mutation"]["item"]["completed"] is True
    missing = execute(registry, call("todo", action="complete", item_id="bad"), state)
    assert missing.error_code == "TODO_NOT_FOUND"


def test_validation_unknown_json_and_demo_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = build_default_registry()
    invalid = execute(registry, call("todo", action="add"))
    assert invalid.error_code == "VALIDATION_ERROR"
    extra = execute(registry, call("calculator", expression="1+1", unexpected=True))
    assert extra.error_code == "VALIDATION_ERROR"
    unknown = execute(registry, call("missing"))
    assert unknown.error_code == "UNKNOWN_TOOL"
    malformed = execute(
        registry, ToolCall(id="bad-json", name="calculator", parse_error="broken")
    )
    assert malformed.error_code == "INVALID_JSON"
    monkeypatch.setenv("GLASSBOX_FAIL_ONCE_TOOL", "search_docs")
    first = execute(registry, call("search_docs", query="周报"))
    second = execute(registry, call("search_docs", query="周报"))
    assert first.error_code == "DEMO_TRANSIENT_FAILURE" and first.retryable
    assert second.ok


class EmptyArgs(BaseModel):
    pass


def test_tool_timeout_and_output_truncation() -> None:
    def slow(_args, _state):
        time.sleep(0.03)
        return "late", {}

    def verbose(_args, _state):
        return "x" * 20, {}

    registry = ToolRegistry()
    registry.register(ToolSpec("slow", "slow", EmptyArgs, slow, timeout_seconds=0.001))
    registry.register(ToolSpec("verbose", "verbose", EmptyArgs, verbose, max_output_chars=5))
    assert execute(registry, call("slow")).error_code == "TOOL_TIMEOUT"
    result = execute(registry, call("verbose"))
    assert result.ok and result.data["truncated"] is True


def test_registered_but_unbound_tool_is_rejected() -> None:
    registry = build_default_registry()
    result = execute(
        registry,
        call("todo", action="list"),
        allowed_names=("calculator",),
    )
    assert result.error_code == "TOOL_NOT_BOUND"
