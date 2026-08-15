"""Turn-scoped tool binding policies.

Tools are registered once in a process-wide catalog. A policy creates an
immutable view containing references to only the tools exposed for one turn.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from typing import Literal, Protocol

from .domain import RuntimeState, ToolRef, TurnToolView
from .tools import ToolRegistry


class ToolBindingPolicy(Protocol):
    def bind(
        self,
        *,
        turn_id: str,
        user_input: str,
        state: RuntimeState,
        registry: ToolRegistry,
        mode: Literal["all", "turn"],
        allowed_names: Collection[str] | None,
        requested_names: Collection[str] | None,
    ) -> TurnToolView: ...


class RuleBasedTurnBindingPolicy:
    """Deterministic namespace routing without an extra model call."""

    policy_version = "v1"

    def bind(
        self,
        *,
        turn_id: str,
        user_input: str,
        state: RuntimeState,
        registry: ToolRegistry,
        mode: Literal["all", "turn"],
        allowed_names: Collection[str] | None,
        requested_names: Collection[str] | None,
    ) -> TurnToolView:
        del state  # Reserved for state-aware policies without changing the interface.
        catalog_names = registry.names()
        catalog_set = set(catalog_names)
        permitted = catalog_set if allowed_names is None else catalog_set & set(allowed_names)

        reasons: dict[str, list[str]] = {}
        if requested_names is not None:
            unknown = set(requested_names) - catalog_set
            if unknown:
                names = ", ".join(sorted(unknown))
                raise ValueError(f"Requested tools are not registered: {names}")
            selected = set(requested_names) & permitted
            strategy: Literal["all", "routed", "explicit"] = "explicit"
            reasons["explicit"] = ["selected by the runtime caller"]
        elif mode == "all":
            selected = permitted
            strategy = "all"
            reasons["all"] = ["GLASSBOX_TOOL_BINDING_MODE=all"]
        else:
            selected, reasons = self._route(user_input, registry)
            selected &= permitted
            strategy = "routed"

        ordered_names = tuple(name for name in catalog_names if name in selected)
        namespaces = tuple(
            dict.fromkeys(registry.get(name).namespace for name in ordered_names)
        )
        refs = tuple(
            ToolRef(name=name, schema_hash=registry.schema_hash((name,)))
            for name in ordered_names
        )
        return TurnToolView(
            turn_id=turn_id,
            refs=refs,
            strategy=strategy,
            catalog_hash=registry.catalog_hash(),
            schema_hash=registry.schema_hash(ordered_names),
            policy_version=self.policy_version,
            namespaces=namespaces,
            reasons=reasons,
            full_catalog_count=len(catalog_names),
        )

    @staticmethod
    def _route(
        user_input: str, registry: ToolRegistry
    ) -> tuple[set[str], dict[str, list[str]]]:
        text = user_input.casefold()
        selected_namespaces: set[str] = set()
        reasons: dict[str, list[str]] = {}

        for spec in registry.specs():
            matches = [tag for tag in spec.tags if tag.casefold() in text]
            if spec.name.casefold() in text:
                matches.append(spec.name)
            if matches:
                selected_namespaces.add(spec.namespace)
                bucket = reasons.setdefault(spec.namespace, [])
                for match in matches:
                    reason = f"matched tag: {match}"
                    if reason not in bucket:
                        bucket.append(reason)

        if re.search(r"\d\s*(?:[+*/%]|-(?=\s*\d))\s*\d", text):
            selected_namespaces.add("math")
            reasons.setdefault("math", []).append("matched arithmetic expression")

        selected = {
            spec.name for spec in registry.specs() if spec.namespace in selected_namespaces
        }
        return selected, reasons
