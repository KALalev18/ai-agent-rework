"""The agent loop in replay mode: no network, no API key."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from study_agent.agent import CHECKER_PREFIX, SYSTEM_PROMPT, Agent
from study_agent.config import Settings
from study_agent.demo import A3
from study_agent.llm import ReplayModel, Usage, cost_usd, script_to_responses
from study_agent.tools import ToolContext


def make_agent(
    ctx: ToolContext, settings: Settings, script: list[dict[str, Any]], approve: bool = True
) -> tuple[Agent, ReplayModel]:
    model = ReplayModel(script_to_responses(script))
    return Agent(ctx, model, settings, lambda p: approve), model


def test_tool_loop_returns_all_results_in_one_user_message(
    ctx: ToolContext, settings: Settings
) -> None:
    agent, model = make_agent(
        ctx,
        settings,
        [
            {"tools": [{"name": "plan_day", "input": {}}, {"name": "get_deadlines", "input": {}}]},
            {"text": "Nothing at risk today."},
        ],
    )
    result = agent.run("Brief me.")
    assert result.answer == "Nothing at risk today."
    assert [c.name for c in result.tool_calls] == ["plan_day", "get_deadlines"]
    second_request = model.requests[1]["messages"]
    results = second_request[-1]["content"]
    assert [r["type"] for r in results] == ["tool_result", "tool_result"]
    assert results[0]["tool_use_id"] == "toolu_scripted_001"


def test_clock_goes_in_the_user_message_not_the_system_prompt(
    ctx: ToolContext, settings: Settings
) -> None:
    agent, model = make_agent(ctx, settings, [{"text": "Hi."}])
    agent.run("Hello")
    first = model.requests[0]["messages"][0]["content"]
    assert first.startswith("Now: Mon 23 Nov 08:00 2026")
    assert "Now:" not in SYSTEM_PROMPT and "23 Nov" not in SYSTEM_PROMPT  # stable, cacheable


def test_invented_fact_triggers_exactly_one_correction(
    ctx: ToolContext, settings: Settings
) -> None:
    agent, model = make_agent(
        ctx,
        settings,
        [
            {"tools": [{"name": "get_deadlines", "input": {"course": "CS-E9001"}}]},
            {"text": "Exam on Mon 14 Dec 09:00."},
            {"text": "Still Mon 14 Dec."},
        ],
    )
    result = agent.run("When is the exam?")
    assert result.corrected
    assert result.caught is not None and len(result.caught.unsupported) == 2
    assert not result.grounding.ok  # the second answer was still wrong, and that is reported
    assert result.rounds == 3
    correction = model.requests[2]["messages"][-1]["content"][0]["text"]
    assert correction.startswith(CHECKER_PREFIX)


def test_flagged_claims_do_not_ground_themselves(ctx: ToolContext, settings: Settings) -> None:
    agent, _ = make_agent(
        ctx,
        settings,
        [{"text": "Due Mon 14 Dec."}, {"text": "Due Mon 14 Dec, as I said."}],
    )
    result = agent.run("When?")
    assert not result.grounding.ok


def test_declined_write_is_reported_to_the_model(ctx: ToolContext, settings: Settings) -> None:
    agent, model = make_agent(
        ctx,
        settings,
        [
            {"tools": [{"name": "complete_item", "input": {"item_id": A3}}]},
            {"text": "OK, left as is."},
        ],
        approve=False,
    )
    result = agent.run("Mark A3 done")
    assert result.tool_calls[0].approved is False
    tool_result = json.loads(model.requests[1]["messages"][-1]["content"][0]["content"])
    assert tool_result["status"] == "declined"
    assert ctx.store.deadline_states().get(A3) is None


def test_tool_errors_are_flagged_for_the_model(ctx: ToolContext, settings: Settings) -> None:
    agent, model = make_agent(
        ctx,
        settings,
        [
            {"tools": [{"name": "complete_item", "input": {"item_id": "D-nope"}}]},
            {"text": "Sorry."},
        ],
    )
    result = agent.run("Mark it done")
    assert result.tool_calls[0].ok is False
    assert model.requests[1]["messages"][-1]["content"][0]["is_error"] is True


def test_round_limit_stops_a_looping_model(ctx: ToolContext, settings: Settings) -> None:
    looping = [{"tools": [{"name": "list_tasks", "input": {}}]}] * 10
    agent, model = make_agent(ctx, replace(settings, max_rounds=3), looping)
    result = agent.run("Loop forever")
    assert result.stop_reason == "max_rounds"
    assert result.rounds == 3 and len(model.requests) == 3
    assert "stopped after 3" in result.answer


def test_refusal_is_handled(ctx: ToolContext, settings: Settings) -> None:
    model = ReplayModel([{"content": [], "stop_reason": "refusal"}])
    result = Agent(ctx, model, settings, lambda p: True).run("?")
    assert result.stop_reason == "refusal"
    assert "declined" in result.answer


def test_chat_history_is_continued(ctx: ToolContext, settings: Settings) -> None:
    agent, model = make_agent(ctx, settings, [{"text": "First."}, {"text": "Second."}])
    first = agent.run("one")
    second = agent.run("two", first.messages)
    assert second.answer == "Second."
    assert len(model.requests[1]["messages"]) == 3


def test_cost_calculation() -> None:
    usage = Usage(input_tokens=1000, output_tokens=500, cache_read=10_000, cache_write=2000)
    # 1000*4 + 500*20 + 2000*5 + 10000*0.2 = 26000 -> per million
    assert cost_usd("claude-opus-5-5", usage) == 0.026
    assert cost_usd("unknown-model", usage) is None
