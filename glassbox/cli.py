"""Typer and Rich user interface for GlassBox Agent."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .domain import EventType
from .provider import DeepSeekProvider, ProviderError
from .runtime import AgentRuntime, RuntimeConfig
from .store import EventStore
from .tools import build_default_registry

app = typer.Typer(no_args_is_help=True, help="Transparent, replayable minimal Agent runtime.")
console = Console()


def _runtime(db_path: str | Path | None = None) -> AgentRuntime:
    load_dotenv()
    path = db_path or os.getenv("GLASSBOX_DB_PATH", ".glassbox/glassbox.db")
    return AgentRuntime(
        EventStore(path),
        DeepSeekProvider(),
        build_default_registry(),
        RuntimeConfig.from_env(),
    )


def _print_state(runtime: AgentRuntime, session_id: str) -> None:
    state = runtime.replay(session_id)
    console.print(
        Panel.fit(
            f"session={session_id}\nstatus={state.status}\n"
            f"events={state.event_count} user_turns={state.user_turns}\n"
            f"todos={len(state.todos)} memory_facts={len(state.memory.facts)}",
            title="Offline replay",
        )
    )
    for item in state.todos.values():
        marker = "✓" if item.completed else "·"
        console.print(f"  {marker} {item.id} {item.title}")


def _print_trace(runtime: AgentRuntime, session_id: str) -> None:
    table = Table(title=f"GlassBox trace · {session_id}", show_lines=False)
    table.add_column("ID", justify="right")
    table.add_column("Turn")
    table.add_column("Seq", justify="right")
    table.add_column("Event")
    table.add_column("Summary", overflow="fold")
    for event in runtime.trace(session_id):
        payload = event.payload
        summary = ""
        if event.event_type is EventType.USER_MESSAGE:
            summary = payload.get("content", "")
        elif event.event_type is EventType.TOOLS_BOUND:
            tools = payload.get("tool_names") or []
            summary = (
                f"strategy={payload.get('strategy')} "
                f"tools={tools or 'none'} "
                f"catalog={payload.get('full_catalog_count')} bound={len(tools)}"
            )
        elif event.event_type is EventType.TOOL_REQUESTED:
            call = payload.get("call", {})
            summary = f"{call.get('name')} {json.dumps(call.get('arguments'), ensure_ascii=False)}"
        elif event.event_type in {EventType.TOOL_SUCCEEDED, EventType.TOOL_FAILED}:
            status = "ok" if payload.get("ok") else payload.get("error_code", "failed")
            summary = (
                f"{payload.get('name')} · {status} · {payload.get('duration_ms', 0)}ms · "
                f"{payload.get('content', '')}"
            )
        elif event.event_type is EventType.LLM_REQUESTED:
            summary = (
                f"step={payload.get('step')} chars≈{payload.get('estimated_chars')} "
                f"tools={payload.get('tool_names') or 'none'} "
                f"schema_chars={payload.get('schema_chars', 0)}"
            )
        elif event.event_type is EventType.LLM_RESPONDED:
            calls = [call.get("name") for call in payload.get("tool_calls", [])]
            summary = (
                f"step={payload.get('step')} tools={calls or 'none'} usage={payload.get('usage')}"
            )
        elif event.event_type is EventType.RETRY_SCHEDULED:
            summary = (
                f"retry={payload.get('retry_number')} {payload.get('error_type')}: "
                f"{payload.get('message')}"
            )
        elif event.event_type is EventType.ASSISTANT_MESSAGE:
            summary = payload.get("content", "")
        elif event.event_type in {
            EventType.CONTEXT_COMPACTED,
            EventType.CONTEXT_COMPACTION_FAILED,
        }:
            summary = f"through_event={payload.get('through_event_id')}"
        elif event.event_type is EventType.RUN_STOPPED:
            summary = payload.get("message", payload.get("reason", ""))
        table.add_row(
            str(event.id),
            event.turn_id[:8],
            str(event.sequence),
            event.event_type.value,
            str(summary)[:300],
        )
    console.print(table)


@app.command()
def doctor() -> None:
    """Verify credentials and DeepSeek function-calling compatibility."""

    load_dotenv()
    try:
        result = DeepSeekProvider().doctor()
    except ProviderError as exc:
        console.print(Panel(str(exc), title="Doctor failed", border_style="red"))
        raise typer.Exit(1) from exc
    console.print(Panel(json.dumps(result, indent=2), title="Doctor passed", border_style="green"))


@app.command()
def chat(
    session: Annotated[str | None, typer.Option(help="Resume an existing session.")] = None,
    db: Annotated[Path | None, typer.Option(help="SQLite database path.")] = None,
) -> None:
    """Start the interactive terminal chat."""

    try:
        runtime = _runtime(db)
    except ProviderError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if session is None:
        session = runtime.store.create_session("Interactive session").id
    else:
        runtime.store.get_session(session)
    console.print(
        Panel.fit(
            f"Session [bold]{session}[/bold]\n"
            "Commands: /new /sessions /use ID /trace /context /replay /fork EVENT "
            "/export [SESSION] [PATH] /quit",
            title="GlassBox Agent",
        )
    )
    while True:
        try:
            value = console.input(f"[bold cyan]{session}>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nBye.")
            return
        if not value:
            continue
        if value == "/quit":
            return
        if value.startswith("/"):
            parts = value.split(maxsplit=2)
            command = parts[0]
            try:
                if command == "/new":
                    title = value.partition(" ")[2].strip() or "Interactive session"
                    session = runtime.store.create_session(title).id
                    console.print(f"Created and selected [green]{session}[/green]")
                elif command == "/sessions":
                    for item in runtime.store.list_sessions():
                        selected = "*" if item.id == session else " "
                        console.print(f"{selected} {item.id}  {item.title}")
                elif command == "/use" and len(parts) > 1:
                    runtime.store.get_session(parts[1])
                    session = parts[1]
                elif command == "/trace":
                    _print_trace(runtime, session)
                elif command == "/context":
                    snapshot = runtime.context_snapshot(session)
                    console.print_json(snapshot.model_dump_json())
                elif command == "/replay":
                    target = parts[1] if len(parts) > 1 else session
                    _print_state(runtime, target)
                elif command == "/fork" and len(parts) > 1:
                    child = runtime.fork(session, int(parts[1]))
                    session = child.id
                    console.print(f"Forked and selected [green]{session}[/green]")
                elif command == "/export":
                    target = parts[1] if len(parts) > 1 else session
                    runtime.store.get_session(target)
                    path = Path(parts[2]) if len(parts) > 2 else Path(f"trace-{target}.jsonl")
                    path.write_text(runtime.export_jsonl(target), encoding="utf-8")
                    console.print(f"Exported {path.resolve()}")
                else:
                    console.print("[yellow]Unknown or incomplete command.[/yellow]")
            except (KeyError, ValueError, RuntimeError) as exc:
                console.print(f"[red]{exc}[/red]")
            continue
        try:
            outcome = runtime.run(session, value)
        except (ValueError, RuntimeError) as exc:
            console.print(f"[red]{exc}[/red]")
            continue
        style = "green" if outcome.status == "completed" else "yellow"
        console.print(
            Panel(outcome.answer, title=f"Agent · {outcome.steps} step(s)", border_style=style)
        )


@app.command()
def trace(
    session_id: str,
    db: Annotated[Path, typer.Option(help="SQLite database path.")] = Path(".glassbox/glassbox.db"),
) -> None:
    """Render the event trace for a session without calling the model."""

    runtime = _runtime_without_provider(db)
    _print_trace(runtime, session_id)


@app.command()
def replay(
    session_id: str,
    db: Annotated[Path, typer.Option(help="SQLite database path.")] = Path(".glassbox/glassbox.db"),
) -> None:
    """Rebuild session state offline without executing tools or the model."""

    runtime = _runtime_without_provider(db)
    _print_state(runtime, session_id)


class _OfflineProvider:
    model = "offline"

    def complete(self, *_args, **_kwargs):  # pragma: no cover - defensive boundary
        raise RuntimeError("Offline commands never call the provider")

    def summarize(self, *_args, **_kwargs):  # pragma: no cover - defensive boundary
        raise RuntimeError("Offline commands never summarize")

    def doctor(self):  # pragma: no cover - defensive boundary
        raise RuntimeError("Offline provider has no doctor")


def _runtime_without_provider(db: str | Path) -> AgentRuntime:
    return AgentRuntime(
        EventStore(db), _OfflineProvider(), build_default_registry(), RuntimeConfig()
    )


@app.command("export")
def export_trace(
    session_id: str,
    output: Path,
    db: Annotated[Path, typer.Option(help="SQLite database path.")] = Path(".glassbox/glassbox.db"),
) -> None:
    """Export inherited and local session events as JSONL."""

    runtime = _runtime_without_provider(db)
    output.write_text(runtime.export_jsonl(session_id), encoding="utf-8")
    console.print(f"Exported {output.resolve()}")


@app.command("eval")
def live_eval() -> None:
    """Run three explicit, paid live DeepSeek scenarios."""

    load_dotenv()
    scenarios = [
        ("direct", "Reply with exactly PONG. Do not call a tool."),
        (
            "tool-chain",
            "查阅公司国内差旅政策，计算上海出差3天的餐补总额，并添加待办“周五提交报销”。",
        ),
        ("recovery", "搜索研发周报模板并告诉我必须包含哪些部分。"),
    ]
    passed = 0
    with tempfile.TemporaryDirectory() as directory:
        runtime = _runtime(Path(directory) / "eval.db")
        for name, prompt in scenarios:
            old_failure = os.getenv("GLASSBOX_FAIL_ONCE_TOOL")
            if name == "recovery":
                os.environ["GLASSBOX_FAIL_ONCE_TOOL"] = "search_docs"
            try:
                session = runtime.store.create_session(f"eval-{name}")
                outcome = runtime.run(session.id, prompt)
                events = runtime.trace(session.id)
                tool_names = {
                    event.payload.get("call", {}).get("name")
                    for event in events
                    if event.event_type is EventType.TOOL_REQUESTED
                }
                ok = outcome.status == "completed"
                if name == "direct":
                    ok = ok and not tool_names and outcome.answer.strip() == "PONG"
                elif name == "tool-chain":
                    ok = (
                        ok
                        and {"search_docs", "read_doc", "calculator", "todo"}.issubset(tool_names)
                        and bool(outcome.state.todos)
                        and "360" in outcome.answer
                    )
                else:
                    failed = any(event.event_type is EventType.TOOL_FAILED for event in events)
                    searched_twice = (
                        sum(
                            1
                            for event in events
                            if event.event_type is EventType.TOOL_REQUESTED
                            and event.payload.get("call", {}).get("name") == "search_docs"
                        )
                        >= 2
                    )
                    expected_facts = all(
                        fact in outcome.answer
                        for fact in ("本周完成", "关键数据", "风险阻塞", "下周计划")
                    )
                    ok = (
                        ok
                        and {"search_docs", "read_doc"}.issubset(tool_names)
                        and failed
                        and searched_twice
                        and expected_facts
                    )
                ok = ok and not outcome.state.pending_tool_calls
                passed += int(ok)
                console.print(f"{'PASS' if ok else 'FAIL'} {name}: {outcome.answer[:100]}")
            finally:
                if old_failure is None:
                    os.environ.pop("GLASSBOX_FAIL_ONCE_TOOL", None)
                else:
                    os.environ["GLASSBOX_FAIL_ONCE_TOOL"] = old_failure
    console.print(f"Live eval: {passed}/{len(scenarios)} passed")
    if passed != len(scenarios):
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
