"""Command-line interface: `study today`, `study week`, `study ask`, `study done`, ..."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import timedelta

from study_agent import render
from study_agent.agent import Agent, AgentResult
from study_agent.config import ConfigError, Settings, load_settings
from study_agent.evals import CaseResult, load_cases, run_case, summarize
from study_agent.llm import AnthropicModel, LLMError
from study_agent.planner import RISKY_ORDER
from study_agent.sources.loader import Calendars, SourceError
from study_agent.stats import compute_stats
from study_agent.store import RunRecord, Store
from study_agent.timeutil import day_bounds, fmt_moment, system_clock
from study_agent.tools import ToolContext

console = render.console

QUESTIONS = {
    "today": "Give me my morning brief for today.",
    "week": "Give me an overview of my next 7 days.",
}
DEMO_STEPS = (
    ("study today", "morning-brief"),
    ('study done "finished exercise round 8, took about 3 hours"', "done-log"),
    ('study ask "When is the CS-E9001 final exam?"', "hallucination-trap"),
)


def _context(settings: Settings, offline: bool) -> tuple[Store, ToolContext]:
    store = Store(settings.db_path)
    calendars = Calendars(settings, system_clock, offline=offline)
    return store, ToolContext(settings, store, calendars, system_clock)


def _record(store: Store, command: str, question: str, result: AgentResult, s: Settings) -> None:
    started = system_clock() - timedelta(milliseconds=result.latency_ms)
    run_id = store.record_run(
        RunRecord(
            command=command,
            mode="live",
            model=s.model,
            question=question,
            answer=result.answer,
            rounds=result.rounds,
            tool_calls=len(result.tool_calls),
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cache_read_tokens=result.usage.cache_read,
            cache_write_tokens=result.usage.cache_write,
            cost_usd=result.cost_usd or 0.0,
            latency_ms=result.latency_ms,
            ungrounded=len(result.grounding.unsupported),
            corrected=result.corrected,
            stop_reason=result.stop_reason,
        ),
        started,
    )
    for call in result.tool_calls:
        store.record_tool_call(
            run_id, call.name, call.input, call.ok, call.approved, call.latency_ms
        )


def run_question(settings: Settings, command: str, question: str, offline: bool) -> int:
    store, ctx = _context(settings, offline)
    try:
        console.print(f"[bold]Study Agent[/] [dim]{fmt_moment(ctx.now())} | {settings.model}[/]")
        agent = Agent(ctx, AnthropicModel(), settings, render.ask_approval, render.trace)
        result = agent.run(question)
        for warning in ctx.calendars.warnings:
            console.print(f"[yellow]  !  {warning}[/]")
        render.show_answer(result, "live", settings.eur_per_usd)
        _record(store, command, question, result, settings)
    finally:
        store.close()
    return 0


def chat(settings: Settings, offline: bool) -> int:
    store, ctx = _context(settings, offline)
    agent = Agent(ctx, AnthropicModel(), settings, render.ask_approval, render.trace)
    history: list[dict[str, object]] = []
    console.print("[bold]Study Agent chat[/] [dim](empty line or 'quit' to exit)[/]")
    try:
        while True:
            try:
                question = console.input("[bold cyan]you> [/]").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if question.lower() in ("", "quit", "exit"):
                break
            result = agent.run(question, history)
            history = result.messages
            render.show_answer(result, "live", settings.eur_per_usd)
            _record(store, "chat", question, result, settings)
    finally:
        store.close()
    return 0


def deadlines(settings: Settings, days: int, offline: bool) -> int:
    """No model involved: the same deterministic data the agent sees, as a table."""
    store, ctx = _context(settings, offline)
    try:
        items = ctx.items()
        risks = ctx.risks(items)
        _, horizon = day_bounds(ctx.today() + timedelta(days=days))
        style = {"overdue": "bold red", "at_risk": "red", "tight": "yellow", "ok": "green"}
        rows = []
        for item in sorted((i for i in items if i.due), key=lambda i: i.due or ctx.now()):
            risk = risks.get(item.id)
            status = risk.status if risk else "-"
            if item.due and item.due > horizon and status not in RISKY_ORDER:
                continue
            rows.append(
                [
                    fmt_moment(item.due) if item.due else "-",
                    item.course,
                    item.title,
                    f"{item.remaining_min / 60:.1f} h",
                    f"[{style.get(status, 'dim')}]{status}[/]",
                    item.id,
                ]
            )
        console.print(
            render.table(
                f"Open deadlines and tasks (next {days} days, plus anything at risk)",
                ["Due", "Course", "Title", "Left", "Risk", "Id"],
                rows,
            )
        )
        for warning in ctx.calendars.warnings:
            console.print(f"[yellow]  !  {warning}[/]")
    finally:
        store.close()
    return 0


def sync(settings: Settings) -> int:
    calendars = Calendars(settings, system_clock)
    counts = calendars.sync(force=True)
    for name, count in counts.items():
        console.print(f"  {name.replace('_', ' ')}: {count}")
    for warning in calendars.warnings:
        console.print(f"[yellow]  !  {warning}[/]")
    console.print("[green]Calendars synced.[/]")
    return 0


def stats(settings: Settings) -> int:
    store = Store(settings.db_path)
    try:
        report = compute_stats(store.runs(mode="live"), settings.eur_per_usd)
    finally:
        store.close()
    if report is None:
        console.print("No live runs logged yet. Run `study today` first.")
        return 0
    console.print(
        render.table(
            "Usage (live runs only)", ["Metric", "Value"], [[k, v] for k, v in report.items()]
        )
    )
    return 0


def _print_case(r: CaseResult) -> None:
    mark = "[green]PASS[/]" if r.passed else "[red]FAIL[/]"
    console.print(f"{mark} {r.case.id} [dim]({r.source})[/]")
    for problem in r.problems:
        console.print(f"[red]     {problem}[/]")


def evaluate(settings: Settings, live: bool, record: bool, only: str | None, verbose: bool) -> int:
    cases = [c for c in load_cases() if not only or c.id == only]
    if not cases:
        console.print(f"[red]No eval case named {only!r}[/]")
        return 2
    mode = "live" if live else "replay"
    console.print(f"[bold]Eval[/] {len(cases)} cases, {mode} mode, model {settings.model}\n")
    results = []
    for case in cases:
        if verbose:
            console.print(f"[bold]> {case.question}[/]")
        result = run_case(
            case,
            settings,
            live=live,
            record=record,
            on_event=render.trace if verbose else None,
            show_proposal=render.auto_decision if verbose else (lambda p, d: None),
        )
        if verbose and result.result:
            render.show_answer(result.result, mode, settings.eur_per_usd)
        _print_case(result)
        results.append(result)
    summary = summarize(results)
    console.print()
    console.print(
        render.table(
            "Eval summary",
            ["Metric", "Value"],
            [[k.replace("_", " "), str(v)] for k, v in summary.items()],
        )
    )
    return 0 if summary["passed"] == summary["cases"] else 1


def demo(settings: Settings) -> int:
    console.print(
        "[bold]Study Agent demo[/]: a fictional semester (made-up course codes), clock frozen at"
        " Mon 23 Nov 2026 08:00.\nModel turns are replayed from scripts, so no API key is"
        " needed. The tools, approval gate, grounding checker and SQLite run for real.\n"
    )
    cases = {c.id: c for c in load_cases()}
    for command, case_id in DEMO_STEPS:
        console.rule(f"[bold]$ {command}")
        result = run_case(
            cases[case_id], settings, on_event=render.trace, show_proposal=render.auto_decision
        )
        if result.result is None:
            console.print(f"[red]Demo step failed: {'; '.join(result.problems)}[/]")
            return 1
        render.show_answer(result.result, "replay", settings.eur_per_usd)
        console.print()
    console.print("Try it on your own calendar: see README, 'Run it on your calendar'.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="study", description="A personal AI agent that plans my study days."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (("today", "morning brief for today"), ("week", "the next 7 days")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--offline", action="store_true", help="use cached calendars only")
    for name, text in (
        ("ask", "ask anything about my deadlines, timetable and plan"),
        ("done", "log something I finished or worked on"),
        ("add", "add a task in plain words"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("text", nargs="+")
        p.add_argument("--offline", action="store_true", help="use cached calendars only")
    p = sub.add_parser("chat", help="multi-turn conversation")
    p.add_argument("--offline", action="store_true")
    p = sub.add_parser("deadlines", help="deadline and risk table, no model call")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--offline", action="store_true")
    sub.add_parser("sync", help="download the calendars now")
    sub.add_parser("stats", help="days used, cost, latency and grounding from the run log")
    sub.add_parser("demo", help="run on a fictional semester without an API key")
    p = sub.add_parser("eval", help="run the evaluation set")
    p.add_argument("--live", action="store_true", help="call the real API (costs money)")
    p.add_argument("--record", action="store_true", help="with --live: save responses")
    p.add_argument("--case", help="run a single case by id")
    p.add_argument("-v", "--verbose", action="store_true", help="show traces and answers")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
        command = args.command
        if command in QUESTIONS:
            return run_question(settings, command, QUESTIONS[command], args.offline)
        if command in ("ask", "done", "add"):
            text = " ".join(args.text)
            question = {
                "ask": text,
                "done": f"I finished or worked on this: {text}. Log it.",
                "add": f"Add this as a task: {text}",
            }[command]
            return run_question(settings, command, question, args.offline)
        if command == "chat":
            return chat(settings, args.offline)
        if command == "deadlines":
            return deadlines(settings, args.days, args.offline)
        if command == "sync":
            return sync(settings)
        if command == "stats":
            return stats(settings)
        if command == "demo":
            return demo(settings)
        if command == "eval":
            if args.record and not args.live:
                console.print("[red]--record needs --live[/]")
                return 2
            return evaluate(settings, args.live, args.record, args.case, args.verbose)
    except (ConfigError, SourceError, LLMError) as exc:
        console.print(f"[red]Error:[/] {exc}")
        return 2
    except KeyboardInterrupt:
        return 130
    return 2


if __name__ == "__main__":
    sys.exit(main())
