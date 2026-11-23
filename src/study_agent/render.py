"""Terminal output with rich: the tool-call trace, approval prompts, answers and run footers."""

from __future__ import annotations

import json
import sys
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from study_agent.agent import AgentResult
from study_agent.tools import Proposal

console = Console(highlight=False)


def _short_args(args: dict[str, Any]) -> str:
    parts = []
    for key, value in args.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if len(text) > 40:
            text = text[:37] + "..."
        parts.append(f"{key}={text}")
    return ", ".join(parts)


def trace(kind: str, data: dict[str, Any]) -> None:
    """Show each step of the loop as it happens, so I can see what the agent is doing."""
    if kind == "tool_call":
        console.print(f"[dim]  -> {data['name']}({_short_args(data['input'])})[/]")
    elif kind == "tool_result":
        if not data["ok"]:
            error = json.loads(data["content"]).get("error", "")
            console.print(f"[yellow]  !  {data['name']} refused: {error}[/]")
        elif data["approved"] is True:
            console.print("[green]  ok applied[/]")
        elif data["approved"] is False:
            console.print("[red]  x  declined, nothing saved[/]")
    elif kind == "grounding_retry":
        console.print(
            f"[yellow]  !  grounding check: {data['claims']} not found in any tool result;"
            " asking the model to correct[/]"
        )


def show_proposal(proposal: Proposal) -> None:
    body = "\n".join(proposal.lines)
    console.print(
        Panel(
            body,
            title=f"[bold]Proposed change: {proposal.action}[/]",
            border_style="cyan",
            expand=False,
        )
    )


def ask_approval(proposal: Proposal) -> bool:
    """The human-in-the-loop gate. Default is No; without a terminal, every change is declined."""
    show_proposal(proposal)
    if not sys.stdin.isatty():
        console.print("[red]  not an interactive terminal: change declined[/]")
        return False
    return Confirm.ask("  Apply this change?", default=False, console=console)


def auto_decision(proposal: Proposal, decision: bool) -> None:
    show_proposal(proposal)
    console.print(f"  Apply this change? [bold]{'y' if decision else 'n'}[/] [dim](scripted)[/]")


def footer(result: AgentResult, mode: str, eur_per_usd: float) -> str:
    u = result.usage
    calls = len(result.tool_calls)
    parts = [
        f"{calls} tool call{'' if calls == 1 else 's'}",
        f"{result.rounds} round{'' if result.rounds == 1 else 's'}",
        f"{result.latency_ms / 1000:.1f} s",
    ]
    if mode == "live":
        parts.append(
            f"{u.input_tokens + u.cache_read + u.cache_write:,} in"
            f" ({u.cache_read:,} cached) / {u.output_tokens:,} out tokens"
        )
        if result.cost_usd is not None:
            parts.append(f"EUR {result.cost_usd * eur_per_usd:.3f}")
    else:
        parts.append("replay: no API call")
    if result.grounding.ok:
        checked = "grounded" + (" after 1 correction" if result.corrected else "")
    else:
        checked = "NOT grounded"
    parts.append(checked)
    return " | ".join(parts)


def show_answer(result: AgentResult, mode: str, eur_per_usd: float) -> None:
    console.print()
    console.print(Markdown(result.answer or "(no answer)"))
    if not result.grounding.ok:
        console.print(
            Panel(
                "These were not in any tool result, so treat them as unverified:\n"
                + result.grounding.describe(),
                title="[bold red]Grounding warning[/]",
                border_style="red",
                expand=False,
            )
        )
    console.print(f"\n[dim]{footer(result, mode, eur_per_usd)}[/]")


def table(title: str, columns: list[str], rows: list[list[str]]) -> Table:
    out = Table(title=title, title_justify="left", show_lines=False, header_style="bold")
    for column in columns:
        out.add_column(column)
    for row in rows:
        out.add_row(*row)
    return out
