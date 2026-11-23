"""Evaluation: scripted questions against the demo semester, scored automatically.

Each case starts from a fresh copy of the demo semester (fixed clock, seeded database) and
checks four things:

1. tools: the expected tools were called and forbidden ones were not (e.g. no write tool for
   a read-only question, no move when the request was ambiguous);
2. facts: the answer contains the expected facts and none of the forbidden ones;
3. grounding: after the optional correction round, no date, time or course code in the answer
   is missing from the tool results;
4. database: SQL assertions on the state afterwards (a declined change must leave no trace).

Replay mode (the default, used in CI) runs a recorded live response if one exists in
``cassettes/``, otherwise the case's hand-written script. Replay tests the harness (tools,
approval gate, checker, scoring) deterministically. Only live mode measures the model.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from study_agent.agent import Agent, AgentResult, EventHook
from study_agent.config import Settings
from study_agent.demo import demo_clock, demo_settings, seed
from study_agent.llm import (
    AnthropicModel,
    ModelClient,
    ReplayModel,
    load_cassette,
    save_cassette,
    script_to_responses,
)
from study_agent.sources.loader import Calendars
from study_agent.store import Store
from study_agent.tools import Proposal, ToolContext

EVAL_DIR = Path(__file__).parent
CASES_FILE = EVAL_DIR / "cases.yaml"
CASSETTE_DIR = EVAL_DIR / "cassettes"


@dataclass(frozen=True)
class Case:
    id: str
    question: str
    script: list[dict[str, Any]]
    expected_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    expected_facts: list[str] = field(default_factory=list)
    forbidden_facts: list[str] = field(default_factory=list)
    approve: list[bool] = field(default_factory=list)
    db: list[dict[str, Any]] = field(default_factory=list)
    expect_caught: bool = False
    note: str = ""


def load_cases(path: Path = CASES_FILE) -> list[Case]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [Case(**entry) for entry in raw]


@dataclass
class CaseResult:
    case: Case
    source: str  # live | recorded | scripted
    tools_ok: bool
    facts_ok: bool
    grounded: bool
    db_ok: bool
    caught: int
    problems: list[str]
    result: AgentResult | None

    @property
    def passed(self) -> bool:
        return self.tools_ok and self.facts_ok and self.grounded and self.db_ok


Approver = Callable[[Proposal], bool]


def _approver(case: Case, problems: list[str], show: Callable[[Proposal, bool], None]) -> Approver:
    answers = iter(case.approve)

    def approve(proposal: Proposal) -> bool:
        decision = next(answers, None)
        if decision is None:
            problems.append(f"unexpected approval request: {proposal.action}")
            decision = False
        show(proposal, decision)
        return decision

    return approve


def _client(
    case: Case, live: bool, record_to: list[dict[str, Any]] | None
) -> tuple[ModelClient, str]:
    if live:
        return AnthropicModel(record_to=record_to), "live"
    cassette = CASSETTE_DIR / f"{case.id}.json"
    if cassette.exists():
        return ReplayModel(load_cassette(cassette)), "recorded"
    return ReplayModel(script_to_responses(case.script)), "scripted"


def run_case(
    case: Case,
    base: Settings,
    live: bool = False,
    record: bool = False,
    on_event: EventHook | None = None,
    show_proposal: Callable[[Proposal, bool], None] = lambda p, d: None,
) -> CaseResult:
    problems: list[str] = []
    recorded: list[dict[str, Any]] | None = [] if (live and record) else None
    client, source = _client(case, live, recorded)
    with tempfile.TemporaryDirectory() as tmp:
        settings = demo_settings(Path(tmp), replace(base, data_dir=Path(tmp)))
        store = Store(settings.db_path)
        try:
            seed(store)
            ctx = ToolContext(settings, store, Calendars(settings, demo_clock), demo_clock)
            agent = Agent(ctx, client, settings, _approver(case, problems, show_proposal), on_event)
            try:
                result: AgentResult | None = agent.run(case.question)
            except Exception as exc:  # a crash is a failed case, not a crashed eval run
                problems.append(f"run failed: {exc}")
                result = None
            db_ok = True
            for check in case.db:
                value = store.conn.execute(check["sql"]).fetchone()[0]
                if value != check["equals"]:
                    db_ok = False
                    problems.append(
                        f"db: {check['sql']} -> {value!r}, expected {check['equals']!r}"
                    )
        finally:
            store.close()

    if result is None:
        return CaseResult(case, source, False, False, False, False, 0, problems, None)

    called = [c.name for c in result.tool_calls]
    # "plan_day|get_deadlines" means either tool is a correct way to answer.
    missing = [t for t in case.expected_tools if not set(t.split("|")) & set(called)]
    forbidden = [t for t in case.forbidden_tools if t in called]
    tools_ok = not missing and not forbidden
    if missing:
        problems.append(f"expected tool(s) not called: {', '.join(missing)}")
    if forbidden:
        problems.append(f"forbidden tool(s) called: {', '.join(forbidden)}")

    answer = result.answer.lower()
    absent = [f for f in case.expected_facts if f.lower() not in answer]
    present = [f for f in case.forbidden_facts if f.lower() in answer]
    facts_ok = not absent and not present
    if absent:
        problems.append(f"missing fact(s): {', '.join(absent)}")
    if present:
        problems.append(f"forbidden fact(s) present: {', '.join(present)}")

    grounded = result.grounding.ok
    if not grounded:
        problems.append(f"ungrounded: {result.grounding.describe()}")
    caught = len(result.caught.unsupported) if result.caught else 0
    if case.expect_caught and source == "scripted" and caught == 0:
        problems.append("the grounding checker should have caught the scripted invention")
        grounded = False
    if problems and any(p.startswith("unexpected approval") for p in problems):
        tools_ok = False

    if recorded is not None and result is not None:
        save_cassette(
            CASSETTE_DIR / f"{case.id}.json",
            recorded,
            {"case": case.id, "model": base.model, "effort": base.effort},
        )
    return CaseResult(case, source, tools_ok, facts_ok, grounded, db_ok, caught, problems, result)


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    finished = [r for r in results if r.result is not None]
    latencies = sorted(r.result.latency_ms for r in finished if r.result)
    costs = [r.result.cost_usd or 0.0 for r in finished if r.result]
    return {
        "cases": len(results),
        "passed": sum(r.passed for r in results),
        "tool_calls_correct": sum(r.tools_ok for r in results),
        "facts_correct": sum(r.facts_ok for r in results),
        "grounded_answers": sum(r.grounded for r in results),
        "ungrounded_claims_after_check": sum(
            len(r.result.grounding.unsupported) for r in finished if r.result
        ),
        "claims_caught_by_checker": sum(r.caught for r in results),
        "db_checks_correct": sum(r.db_ok for r in results),
        "total_cost_usd": round(sum(costs), 4),
        "latency_p50_ms": latencies[len(latencies) // 2] if latencies else 0,
        "sources": sorted({r.source for r in results}),
    }
