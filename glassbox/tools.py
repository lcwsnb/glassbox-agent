"""Schema-driven tool registry and the four built-in demo tools."""

from __future__ import annotations

import ast
import hashlib
import json
import operator
import os
import re
import time
import uuid
from collections.abc import Callable, Collection
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .domain import RuntimeState, ToolCall, ToolResult


class ToolExecutionError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


ToolHandler = Callable[[BaseModel, RuntimeState], tuple[str, dict[str, Any]]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: ToolHandler
    namespace: str = "default"
    tags: tuple[str, ...] = ()
    timeout_seconds: float = 5.0
    max_output_chars: int = 8_000


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._demo_failures: set[str] = set()
        self._frozen = False

    def register(self, spec: ToolSpec) -> None:
        if self._frozen:
            raise RuntimeError("Tool registry is frozen")
        if spec.name in self._tools:
            raise ValueError(f"Tool '{spec.name}' is already registered")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", spec.name):
            raise ValueError(f"Invalid tool name: {spec.name!r}")
        self._tools[spec.name] = spec

    def freeze(self) -> None:
        self._frozen = True

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._tools.values())

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name}") from exc

    def schemas(self, names: Collection[str] | None = None) -> list[dict[str, Any]]:
        selected_names = self.names() if names is None else tuple(dict.fromkeys(names))
        unknown = set(selected_names) - set(self._tools)
        if unknown:
            raise KeyError(f"Unknown tools: {', '.join(sorted(unknown))}")
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.args_model.model_json_schema(),
                },
            }
            for name in selected_names
            for spec in (self._tools[name],)
        ]

    def schema_hash(self, names: Collection[str]) -> str:
        encoded = json.dumps(
            self.schemas(names), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]

    def catalog_hash(self) -> str:
        return self.schema_hash(self.names())

    def execute(
        self,
        call: ToolCall,
        state: RuntimeState,
        *,
        allowed_names: Collection[str],
    ) -> ToolResult:
        started = time.perf_counter()
        spec = self._tools.get(call.name)
        if spec is None:
            return self._failure(call, started, "UNKNOWN_TOOL", f"Unknown tool: {call.name}")
        if call.name not in allowed_names:
            return self._failure(
                call,
                started,
                "TOOL_NOT_BOUND",
                f"Tool '{call.name}' is not available in the current turn",
            )
        if call.parse_error:
            return self._failure(
                call,
                started,
                "INVALID_JSON",
                f"Tool arguments are not valid JSON: {call.parse_error}",
            )
        fail_once = os.getenv("GLASSBOX_FAIL_ONCE_TOOL", "").strip()
        if fail_once == call.name and call.name not in self._demo_failures:
            self._demo_failures.add(call.name)
            return self._failure(
                call,
                started,
                "DEMO_TRANSIENT_FAILURE",
                "Injected one-time demo failure. Retry the same operation once.",
                retryable=True,
            )
        try:
            args = spec.args_model.model_validate(call.arguments)
        except ValidationError as exc:
            return self._failure(call, started, "VALIDATION_ERROR", str(exc))
        try:
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(spec.handler, args, state)
            try:
                content, data = future.result(timeout=spec.timeout_seconds)
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
        except FutureTimeoutError:
            return self._failure(
                call,
                started,
                "TOOL_TIMEOUT",
                f"Tool exceeded {spec.timeout_seconds:g}s timeout",
                retryable=True,
            )
        except ToolExecutionError as exc:
            return self._failure(call, started, exc.code, str(exc), retryable=exc.retryable)
        except Exception as exc:  # noqa: BLE001 - tools are a runtime boundary
            return self._failure(call, started, "TOOL_EXCEPTION", str(exc))
        if len(content) > spec.max_output_chars:
            content = content[: spec.max_output_chars] + "\n[tool output truncated]"
            data = {**data, "truncated": True}
        return ToolResult(
            call_id=call.id,
            name=call.name,
            ok=True,
            content=content,
            data=data,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    @staticmethod
    def _failure(
        call: ToolCall,
        started: float,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> ToolResult:
        content = json.dumps(
            {"ok": False, "error_code": code, "message": message, "retryable": retryable},
            ensure_ascii=False,
        )
        return ToolResult(
            call_id=call.id,
            name=call.name,
            ok=False,
            content=content,
            error_code=code,
            retryable=retryable,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


class CalculatorArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expression: str = Field(min_length=1, max_length=200)


_BINARY_OPERATORS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _evaluate(node: ast.AST) -> int | float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 10:
            raise ToolExecutionError("UNSAFE_EXPRESSION", "Exponent magnitude must be <= 10")
        return _BINARY_OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _UNARY_OPERATORS[type(node.op)](_evaluate(node.operand))
    raise ToolExecutionError(
        "UNSAFE_EXPRESSION", "Only numeric literals and basic arithmetic operators are allowed"
    )


def calculator(args: CalculatorArgs, _state: RuntimeState) -> tuple[str, dict[str, Any]]:
    try:
        parsed = ast.parse(args.expression, mode="eval")
        value = _evaluate(parsed)
    except (SyntaxError, ZeroDivisionError, OverflowError) as exc:
        raise ToolExecutionError("CALCULATION_ERROR", str(exc)) from exc
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return json.dumps({"expression": args.expression, "result": value}), {"result": value}


DOCUMENTS = {
    "travel-domestic": {
        "title": "国内差旅与餐补政策",
        "keywords": ["差旅", "餐补", "上海", "出差", "报销"],
        "content": (
            "国内出差餐补按自然日计算。北京、上海、广州、深圳标准为每人每天120元，"
            "其他城市为每人每天100元。餐补无需提供餐饮发票，但须在返程后5个工作日内提交报销。"
        ),
    },
    "travel-transport": {
        "title": "差旅交通与住宿政策",
        "keywords": ["交通", "住宿", "高铁", "酒店", "差旅"],
        "content": (
            "员工可乘坐高铁二等座或经济舱。上海住宿限额为每晚600元，超出部分需部门负责人审批。"
        ),
    },
    "weekly-report": {
        "title": "研发周报模板",
        "keywords": ["周报", "研发", "风险", "计划"],
        "content": "周报包含本周完成、关键数据、风险阻塞和下周计划四部分。",
    },
}


class SearchDocsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=3, ge=1, le=5)


def search_docs(args: SearchDocsArgs, _state: RuntimeState) -> tuple[str, dict[str, Any]]:
    query = args.query.casefold()
    terms = set(re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}", query))
    ranked = []
    for doc_id, document in DOCUMENTS.items():
        haystack = (
            f"{document['title']} {document['content']} {' '.join(document['keywords'])}".casefold()
        )
        score = sum(2 for keyword in document["keywords"] if keyword.casefold() in query)
        score += sum(1 for term in terms if term in haystack)
        if query in haystack:
            score += 4
        if score:
            ranked.append(
                {
                    "doc_id": doc_id,
                    "title": document["title"],
                    "score": score,
                    "snippet": document["content"][:80],
                }
            )
    ranked.sort(key=lambda item: (-item["score"], item["doc_id"]))
    results = ranked[: args.limit]
    return json.dumps({"results": results}, ensure_ascii=False), {"results": results}


class ReadDocArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str = Field(min_length=1, max_length=100)


def read_doc(args: ReadDocArgs, _state: RuntimeState) -> tuple[str, dict[str, Any]]:
    document = DOCUMENTS.get(args.doc_id)
    if document is None:
        raise ToolExecutionError("DOC_NOT_FOUND", f"Document '{args.doc_id}' does not exist")
    data = {"doc_id": args.doc_id, **document}
    return json.dumps(data, ensure_ascii=False), data


class TodoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["add", "list", "complete"]
    title: str | None = Field(default=None, max_length=200)
    item_id: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def validate_action_fields(self) -> TodoArgs:
        if self.action == "add" and not (self.title or "").strip():
            raise ValueError("title is required when action='add'")
        if self.action == "complete" and not (self.item_id or "").strip():
            raise ValueError("item_id is required when action='complete'")
        return self


def todo(args: TodoArgs, state: RuntimeState) -> tuple[str, dict[str, Any]]:
    if args.action == "list":
        items = [item.model_dump(mode="json") for item in state.todos.values()]
        return json.dumps({"items": items}, ensure_ascii=False), {"items": items}
    if args.action == "add":
        now = datetime.now(UTC).isoformat()
        item = {
            "id": uuid.uuid4().hex[:8],
            "title": args.title.strip(),
            "completed": False,
            "created_at": now,
            "completed_at": None,
        }
        data = {"todo_mutation": {"action": "add", "item": item}}
        return json.dumps({"added": item}, ensure_ascii=False), data
    item = state.todos.get(args.item_id or "")
    if item is None:
        raise ToolExecutionError("TODO_NOT_FOUND", f"Todo '{args.item_id}' does not exist")
    completed = item.model_copy(
        update={"completed": True, "completed_at": datetime.now(UTC).isoformat()}
    )
    completed_data = completed.model_dump(mode="json")
    data = {"todo_mutation": {"action": "complete", "item": completed_data}}
    return json.dumps({"completed": completed_data}, ensure_ascii=False), data


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="calculator",
            description="Safely evaluate basic arithmetic. Use it instead of mental arithmetic.",
            args_model=CalculatorArgs,
            handler=calculator,
            namespace="math",
            tags=("计算", "金额", "总计", "合计", "多少", "算一下", "算", "calculate", "total"),
        )
    )
    registry.register(
        ToolSpec(
            name="search_docs",
            description=(
                "Search the bundled mock company knowledge base. Results are document summaries; "
                "call read_doc for authoritative details."
            ),
            args_model=SearchDocsArgs,
            handler=search_docs,
            namespace="docs",
            tags=(
                "差旅",
                "政策",
                "报销",
                "文档",
                "搜索",
                "查阅",
                "餐补",
                "周报",
                "模板",
                "policy",
                "document",
                "search",
            ),
        )
    )
    registry.register(
        ToolSpec(
            name="read_doc",
            description="Read one bundled document by the doc_id returned by search_docs.",
            args_model=ReadDocArgs,
            handler=read_doc,
            namespace="docs",
            tags=("阅读", "详情", "文档", "政策", "read"),
        )
    )
    registry.register(
        ToolSpec(
            name="todo",
            description="Add, list, or complete session-local todo items.",
            args_model=TodoArgs,
            handler=todo,
            namespace="productivity",
            tags=("待办", "任务", "提醒", "完成", "记下", "记录", "todo"),
        )
    )
    return registry
