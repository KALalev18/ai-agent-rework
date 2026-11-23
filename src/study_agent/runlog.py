"""Write one agent run (and its tool calls) to the run log. Shared by the CLI and the web app."""

from __future__ import annotations

from datetime import timedelta

from study_agent.agent import AgentResult
from study_agent.config import Settings
from study_agent.store import RunRecord, Store
from study_agent.timeutil import system_clock


def log_run(
    store: Store, command: str, question: str, result: AgentResult, settings: Settings
) -> None:
    started = system_clock() - timedelta(milliseconds=result.latency_ms)
    run_id = store.record_run(
        RunRecord(
            command=command,
            mode="live",
            model=settings.model,
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
