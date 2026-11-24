"""The agent loop: model -> tool calls -> my code runs them -> results back -> repeat.

It is a plain ``for`` loop on purpose. Every step is visible: the round limit, the approval gate
for write tools, the grounding check, and the one correction round when the check fails. No
agent framework hides any of it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from study_agent.config import Settings
from study_agent.grounding import FactIndex, GroundingReport, check
from study_agent.llm import ModelClient, Usage, cost_usd
from study_agent.timeutil import fmt_moment
from study_agent.tools import TOOLS_BY_NAME, Approver, ToolContext, execute, tool_definitions

JSON = dict[str, Any]
MAX_TOKENS = 16000
CHECKER_PREFIX = "[grounding check]"

SYSTEM_PROMPT = """\
You are Study Agent, my personal study planner. I study at Aalto University in Finland. You \
answer from my calendars, my task list and my progress log, which you can only see through \
tools, and you help me decide what to work on.

How to work
- Use tools for every fact about deadlines, lectures, tasks, plans and progress. If no tool \
result contains a fact, say you don't know. Never fill gaps from memory.
- In your answer, copy dates, times, weekdays and course codes from tool results; do not work \
out new dates in the text. For tool inputs, turn words like "Wednesday" or "next week" into ISO \
dates based on the current time I give you. All times are Europe/Helsinki.
- Write dates like "Wed 25 Nov 23:59". When you mention a deadline, give the course code, \
title, due time and source, e.g. "CS-E4780 Assignment 3, due Wed 25 Nov 23:59 (mycourses.ics)".
- Risk levels (overdue, at_risk, tight, ok) come from the tools. Report them with the reason; \
do not invent your own.
- Changes go through write tools only: add_task, add_deadline, complete_item, log_progress, \
set_estimate, schedule_blocks, move_study_block, delete_study_block. I approve each change. If I \
decline, accept it and do not retry. Only save study blocks when I ask you to.
- When I say I finished or worked on something, find the matching item with a read tool first, \
then propose log_progress (with mark_done if I said it is finished).
- Deadlines with source "my courses" are ones I typed in myself; treat them like calendar \
ones.
- When you recommend what to work on, follow the deadlines and risk levels first, and mention \
grade_weight_pct when it changes the advice (a 40% project matters more than a 5% quiz due the \
same week). Never guess a weight that no tool result gives.
- If a request is ambiguous (for example two blocks could match), ask one short question.

Morning brief (when I ask about today)
Call plan_day and get_deadlines for the next 7 days, then answer in this shape:
**Today**: my lectures and sessions, one line each.
**Plan**: the suggested study blocks and what each one is for.
**At risk**: overdue, at_risk and tight items with the reason in a few words, or "Nothing at risk."
**Coming up**: other open deadlines in the next 7 days.
End with at most one practical suggestion.

For a week overview, call plan_week and summarise each day in one line, then the risks.

Style: brief and plain, for a terminal. Bold labels and short bullet lists; no tables, no \
greetings, no filler.
"""


@dataclass(frozen=True)
class ToolCallRecord:
    name: str
    input: JSON
    ok: bool
    approved: bool | None
    latency_ms: int

    @property
    def writes(self) -> bool:
        tool = TOOLS_BY_NAME.get(self.name)
        return bool(tool and tool.writes)


@dataclass
class AgentResult:
    answer: str
    stop_reason: str
    rounds: int
    tool_calls: list[ToolCallRecord]
    usage: Usage
    cost_usd: float | None
    latency_ms: int
    grounding: GroundingReport
    caught: GroundingReport | None  # what the checker flagged before the correction round
    messages: list[JSON] = field(default_factory=list)

    @property
    def corrected(self) -> bool:
        return self.caught is not None


EventHook = Callable[[str, JSON], None]


def _block_dict(block: Any) -> JSON:
    return dict(block) if isinstance(block, dict) else dict(block.model_dump())


def _user_texts(messages: list[JSON]) -> list[str]:
    """Everything my side put in front of the model: questions, the clock and tool results.

    Grounding-check messages are excluded, otherwise a flagged claim would ground itself.
    """
    texts: list[str] = []
    for message in messages:
        if message["role"] != "user":
            continue
        content = message["content"]
        if isinstance(content, str):
            texts.append(content)
            continue
        for block in content:
            if block.get("type") == "text" and not block["text"].startswith(CHECKER_PREFIX):
                texts.append(block["text"])
            elif block.get("type") == "tool_result":
                inner = block.get("content", "")
                texts.append(inner if isinstance(inner, str) else str(inner))
    return texts


class Agent:
    def __init__(
        self,
        ctx: ToolContext,
        client: ModelClient,
        settings: Settings,
        approve: Approver,
        on_event: EventHook | None = None,
    ) -> None:
        self.ctx = ctx
        self.client = client
        self.settings = settings
        self.approve = approve
        self.on_event = on_event or (lambda kind, data: None)
        self.tools = tool_definitions()

    def _header(self) -> str:
        # The clock goes in the user message, not the system prompt: a timestamp in the system
        # prompt would change every request and break prompt caching.
        now = self.ctx.now()
        return f"Now: {fmt_moment(now)} {now.year} (Europe/Helsinki)."

    def run(self, question: str, history: list[JSON] | None = None) -> AgentResult:
        started = time.perf_counter()
        messages = list(history or [])
        messages.append({"role": "user", "content": f"{self._header()}\n\n{question}"})
        usage = Usage()
        calls: list[ToolCallRecord] = []
        caught: GroundingReport | None = None
        answer, stop_reason = "", "max_rounds"
        report = GroundingReport()
        rounds = 0

        while rounds < self.settings.max_rounds:
            rounds += 1
            response = self.client.create(
                model=self.settings.model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                tools=self.tools,
                messages=messages,
                effort=self.settings.effort,
            )
            usage += response.usage
            content = [_block_dict(b) for b in response.content]
            # Append the full content (thinking blocks included) so the next turn is valid.
            messages.append({"role": "assistant", "content": content})

            if response.stop_reason == "refusal":
                answer, stop_reason = "The model declined to answer this request.", "refusal"
                break
            if response.stop_reason == "pause_turn":
                continue

            tool_uses = [b for b in content if b.get("type") == "tool_use"]
            if response.stop_reason == "tool_use" and tool_uses:
                results: list[JSON] = []
                for block in tool_uses:
                    args = block.get("input") or {}
                    self.on_event("tool_call", {"name": block["name"], "input": args})
                    outcome = execute(block["name"], args, self.ctx, self.approve)
                    calls.append(
                        ToolCallRecord(
                            block["name"],
                            args,
                            not outcome.is_error,
                            outcome.approved,
                            outcome.latency_ms,
                        )
                    )
                    self.on_event(
                        "tool_result",
                        {
                            "name": block["name"],
                            "ok": not outcome.is_error,
                            "approved": outcome.approved,
                            "content": outcome.content,
                        },
                    )
                    result: JSON = {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": outcome.content,
                    }
                    if outcome.is_error:
                        result["is_error"] = True
                    results.append(result)
                # All results of one turn go back in a single user message.
                messages.append({"role": "user", "content": results})
                continue

            answer = "\n".join(b["text"] for b in content if b.get("type") == "text").strip()
            stop_reason = response.stop_reason
            facts = FactIndex(self.ctx.today())
            facts.add_all(_user_texts(messages))
            report = check(answer, facts)
            if report.ok or caught is not None or rounds >= self.settings.max_rounds:
                break
            # One correction round: tell the model exactly which claims had no source.
            caught = report
            self.on_event("grounding_retry", {"claims": report.describe()})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"{CHECKER_PREFIX} Your answer contains {report.describe()},"
                            " which do not appear in any tool result. Check with the tools and"
                            " rewrite the whole answer using only facts from tool results.",
                        }
                    ],
                }
            )

        if stop_reason == "max_rounds" and not answer:
            answer = (
                f"I stopped after {self.settings.max_rounds} model rounds without a final answer."
            )
        return AgentResult(
            answer=answer,
            stop_reason=stop_reason,
            rounds=rounds,
            tool_calls=calls,
            usage=usage,
            cost_usd=cost_usd(self.settings.model, usage) if self.client.mode == "live" else 0.0,
            latency_ms=int((time.perf_counter() - started) * 1000),
            grounding=report,
            caught=caught,
            messages=messages,
        )
