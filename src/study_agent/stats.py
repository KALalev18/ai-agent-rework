"""Turn the run log into the numbers I report: days used, cost per day, latency, grounding."""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Sequence
from datetime import datetime

from study_agent.timeutil import to_local


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile: simple, and exact for small samples."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def compute_stats(runs: Sequence[sqlite3.Row], eur_per_usd: float) -> dict[str, str] | None:
    if not runs:
        return None
    days = sorted({to_local(datetime.fromisoformat(r["started_at"])).date() for r in runs})
    today_latency = [r["latency_ms"] / 1000 for r in runs if r["command"] == "today"]
    total_cost = sum(r["cost_usd"] for r in runs) * eur_per_usd
    fresh = sum(r["input_tokens"] + r["cache_write_tokens"] for r in runs)
    cached = sum(r["cache_read_tokens"] for r in runs)
    return {
        "live runs": str(len(runs)),
        "days used": f"{len(days)} ({days[0]:%d %b} to {days[-1]:%d %b %Y})",
        "cost total": f"EUR {total_cost:.2f}",
        "cost per day used": f"EUR {total_cost / len(days):.3f}",
        "cost per run": f"EUR {total_cost / len(runs):.4f}",
        "`study today` latency p50 / p95": (
            f"{percentile(today_latency, 50):.1f} s / {percentile(today_latency, 95):.1f} s"
            f" (n={len(today_latency)})"
            if today_latency
            else "no runs yet"
        ),
        "input tokens served from cache": (
            f"{cached / (fresh + cached):.0%}" if fresh + cached else "-"
        ),
        "avg tool calls per run": f"{sum(r['tool_calls'] for r in runs) / len(runs):.1f}",
        "answers corrected by the grounding check": str(sum(r["corrected"] for r in runs)),
        "answers still ungrounded after correction": str(sum(1 for r in runs if r["ungrounded"])),
    }
