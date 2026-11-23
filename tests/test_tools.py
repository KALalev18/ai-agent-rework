"""The approval gate and tool behaviour. The key property: no write without an explicit yes."""

from __future__ import annotations

import json

import pytest

from study_agent.demo import A3, LAB4, MILESTONE2
from study_agent.store import Store
from study_agent.tools import TOOLS, Proposal, ToolContext, execute, tool_definitions


def snapshot(store: Store) -> dict[str, list[tuple[object, ...]]]:
    tables = ("tasks", "deadline_state", "progress", "blocks")
    return {
        t: [tuple(r) for r in store.conn.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in tables
    }


def never(_: Proposal) -> bool:
    raise AssertionError("a read-only tool asked for approval")


WRITE_CALLS = [
    ("add_task", {"title": "Write intro", "course": "CS-E9001", "estimate_min": 60}),
    ("complete_item", {"item_id": A3}),
    ("log_progress", {"text": "did stuff", "item_id": A3, "minutes": 30, "mark_done": True}),
    ("set_estimate", {"item_id": LAB4, "estimate_min": 600}),
    ("schedule_blocks", {"blocks": [{"item_id": A3, "start": "2026-11-23T09:00", "minutes": 60}]}),
    ("move_study_block", {"block_id": "B-2", "new_start": "2026-11-29T13:00"}),
    ("delete_study_block", {"block_id": "B-1"}),
]


@pytest.mark.parametrize(("name", "args"), WRITE_CALLS)
def test_declined_write_changes_nothing(
    ctx: ToolContext, name: str, args: dict[str, object]
) -> None:
    before = snapshot(ctx.store)
    asked: list[Proposal] = []
    outcome = execute(name, args, ctx, lambda p: asked.append(p) is not None)
    assert len(asked) == 1
    assert outcome.approved is False
    assert json.loads(outcome.content)["status"] == "declined"
    assert snapshot(ctx.store) == before


@pytest.mark.parametrize(("name", "args"), WRITE_CALLS)
def test_approved_write_is_applied(ctx: ToolContext, name: str, args: dict[str, object]) -> None:
    before = snapshot(ctx.store)
    outcome = execute(name, args, ctx, lambda p: True)
    assert not outcome.is_error, outcome.content
    payload = json.loads(outcome.content)
    assert payload["status"] == "applied"
    assert payload["change"]  # the approved change is echoed back to the model
    assert snapshot(ctx.store) != before


@pytest.mark.parametrize("tool", [t for t in TOOLS if not t.writes], ids=lambda t: t.name)
def test_read_tools_never_ask_and_never_write(ctx: ToolContext, tool: object) -> None:
    before = snapshot(ctx.store)
    outcome = execute(tool.name, {}, ctx, never)  # type: ignore[attr-defined]
    assert not outcome.is_error, outcome.content
    assert outcome.approved is None
    assert snapshot(ctx.store) == before


def test_every_write_tool_is_marked_and_described_as_needing_approval() -> None:
    for tool in TOOLS:
        assert tool.writes == ("approve" in tool.description)


def test_invalid_inputs_return_errors_without_asking(ctx: ToolContext) -> None:
    cases = [
        ("get_deadlines", {"from_date": "next week"}),
        ("get_deadlines", {"from_date": "2026-12-01", "to_date": "2026-11-01"}),
        ("complete_item", {"item_id": "D-nothere"}),
        ("add_task", {}),
        ("set_estimate", {"item_id": LAB4, "estimate_min": "lots"}),
        ("schedule_blocks", {"blocks": []}),
        ("move_study_block", {"block_id": "B-99", "new_start": "2026-11-29T13:00"}),
        ("no_such_tool", {}),
    ]
    for name, args in cases:
        outcome = execute(name, args, ctx, never)
        assert outcome.is_error, (name, args)
        assert "error" in json.loads(outcome.content)


def test_block_overlapping_a_lecture_is_refused(ctx: ToolContext) -> None:
    blocks = [{"item_id": A3, "start": "2026-11-23T10:00", "minutes": 60}]
    outcome = execute("schedule_blocks", {"blocks": blocks}, ctx, never)
    assert outcome.is_error
    assert "overlaps CS-E9001 lecture" in outcome.content


def test_block_after_the_deadline_is_refused(ctx: ToolContext) -> None:
    outcome = execute(
        "move_study_block", {"block_id": "B-1", "new_start": "2026-11-28T10:00"}, ctx, never
    )
    assert outcome.is_error
    assert "Fri 27 Nov 23:59" in outcome.content


def test_blocks_in_the_past_or_overlapping_each_other_are_refused(ctx: ToolContext) -> None:
    past = [{"item_id": A3, "start": "2026-11-22T10:00", "minutes": 60}]
    assert execute("schedule_blocks", {"blocks": past}, ctx, never).is_error
    pair = [
        {"item_id": MILESTONE2, "start": "2026-11-24T09:00", "minutes": 60},
        {"item_id": MILESTONE2, "start": "2026-11-24T09:30", "minutes": 60},
    ]
    assert execute("schedule_blocks", {"blocks": pair}, ctx, never).is_error


def test_logged_minutes_reduce_remaining_effort(ctx: ToolContext) -> None:
    execute("log_progress", {"text": "x", "item_id": LAB4, "minutes": 120}, ctx, lambda p: True)
    result = json.loads(execute("get_deadlines", {"course": "ELEC-C9003"}, ctx, never).content)
    lab = next(d for d in result["deadlines"] if d["id"] == LAB4)
    assert (lab["estimate_min"], lab["logged_min"], lab["remaining_min"]) == (480, 120, 360)


def test_done_deadlines_are_hidden_unless_requested(ctx: ToolContext) -> None:
    args = {"from_date": "2026-11-01", "to_date": "2026-11-30", "course": "CS-E9001"}
    hidden = json.loads(execute("get_deadlines", args, ctx, never).content)
    shown = json.loads(execute("get_deadlines", {**args, "include_done": True}, ctx, never).content)
    assert "Assignment 2: Prompt evaluation" not in json.dumps(hidden)
    assert any(d.get("done") for d in shown["deadlines"])


def test_tool_results_only_contain_allowed_fields(ctx: ToolContext) -> None:
    """Privacy: course code, title, times, estimates and a source label; never a URL or path."""
    for tool in TOOLS:
        if tool.writes:
            continue
        content = execute(tool.name, {}, ctx, never).content
        assert "http" not in content and ctx.settings.data_dir.name not in content
        assert "Demo text that the parser must never read" not in content


def test_tool_definitions_are_deterministic() -> None:
    assert json.dumps(tool_definitions()) == json.dumps(tool_definitions())
    assert len({t["name"] for t in tool_definitions()}) == len(TOOLS)
