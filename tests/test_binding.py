from __future__ import annotations

import pytest

from glassbox.binding import RuleBasedTurnBindingPolicy
from glassbox.domain import RuntimeState
from glassbox.tools import build_default_registry


def bind(text: str, **kwargs):
    registry = build_default_registry()
    return RuleBasedTurnBindingPolicy().bind(
        turn_id="turn-1",
        user_input=text,
        state=RuntimeState(),
        registry=registry,
        mode=kwargs.pop("mode", "turn"),
        allowed_names=kwargs.pop("allowed_names", None),
        requested_names=kwargs.pop("requested_names", None),
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("查阅研发周报模板", ("search_docs", "read_doc")),
        ("计算 120 * 3", ("calculator",)),
        ("添加一个待办提醒", ("todo",)),
        ("你好", ()),
    ],
)
def test_rule_based_namespace_routing(text: str, expected: tuple[str, ...]) -> None:
    view = bind(text)
    assert view.names == expected
    assert view.strategy == "routed"
    assert view.catalog_hash and view.schema_hash


def test_arithmetic_regex_explicit_all_and_permissions() -> None:
    assert bind("求 8/2").names == ("calculator",)
    assert set(bind("hello", mode="all").names) == {
        "calculator",
        "search_docs",
        "read_doc",
        "todo",
    }
    view = bind(
        "ignored",
        requested_names={"calculator", "todo"},
        allowed_names={"todo"},
    )
    assert view.names == ("todo",)
    assert view.strategy == "explicit"


def test_unknown_explicit_tool_is_rejected() -> None:
    with pytest.raises(ValueError, match="not registered"):
        bind("ignored", requested_names={"missing"})
