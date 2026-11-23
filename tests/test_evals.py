"""The evaluation set runs in CI in replay mode. Each case is its own test."""

from __future__ import annotations

import pytest

from study_agent.config import Settings
from study_agent.evals import Case, load_cases, run_case, summarize

CASES = load_cases()


def test_eval_set_has_about_twenty_unique_cases() -> None:
    ids = [c.id for c in CASES]
    assert len(ids) >= 20
    assert len(set(ids)) == len(ids)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_case_passes_in_replay(case: Case, settings: Settings) -> None:
    result = run_case(case, settings)
    assert result.passed, result.problems


def test_scoring_catches_a_wrong_answer(settings: Settings) -> None:
    case = next(c for c in CASES if c.id == "next-exam")
    wrong = Case(
        id="wrong",
        question=case.question,
        script=[case.script[0], {"text": "Your next exam is CS-E9001 on Mon 14 Dec 09:00."}],
        expected_tools=case.expected_tools,
        expected_facts=case.expected_facts,
    )
    result = run_case(wrong, settings)
    assert not result.passed
    assert not result.facts_ok and not result.grounded
    assert summarize([result])["passed"] == 0


def test_unexpected_approval_request_fails_the_case(settings: Settings) -> None:
    case = Case(
        id="sneaky-write",
        question="What is due?",
        script=[
            {"tools": [{"name": "add_task", "input": {"title": "x"}}]},
            {"text": "Done."},
        ],
    )
    result = run_case(case, settings)
    assert not result.tools_ok
    assert any("unexpected approval" in p for p in result.problems)
