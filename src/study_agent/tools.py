"""The agent's tools: JSON schemas the model sees, and the Python that runs them.

Read-only tools run freely. Write tools never touch the database directly: they validate the
input and return a Proposal. The agent loop shows the proposal, asks me y/n, and only then calls
``proposal.apply()``. That makes "no write without approval" a property of the code structure,
not a promise in the prompt.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from study_agent import courses
from study_agent.config import Settings
from study_agent.models import Deadline, Event
from study_agent.planner import (
    RISKY_ORDER,
    Risk,
    Suggestion,
    WorkItem,
    assess_risks,
    build_items,
    suggest_blocks,
)
from study_agent.sources.loader import Calendars, SourceError
from study_agent.store import Block, Store, block_ref, parse_ref
from study_agent.timeutil import (
    Clock,
    day_bounds,
    end_of_day,
    fmt_day,
    fmt_moment,
    iso,
    minutes_between,
    parse_day,
    parse_moment,
)

MAX_RANGE_DAYS = 120
JSON = dict[str, Any]


class ToolError(Exception):
    """Bad tool input. The message goes back to the model so it can correct the call."""


@dataclass
class Proposal:
    action: str
    lines: list[str]
    apply: Callable[[], JSON]


@dataclass
class ToolContext:
    settings: Settings
    store: Store
    calendars: Calendars
    clock: Clock

    def now(self) -> datetime:
        return self.clock()

    def today(self) -> date:
        return self.now().date()

    def deadlines(self) -> list[Deadline]:
        """Imported calendar deadlines plus the deadlines I typed in for my courses."""
        merged = self.calendars.deadlines() + courses.deadlines(self.store)
        return sorted(merged, key=lambda d: (d.due, d.course, d.title))

    def events(self, start: date, end: date) -> list[Event]:
        """Imported timetable events plus my courses' weekly classes."""
        merged = self.calendars.events(start, end) + courses.events(self.store, start, end)
        return sorted(merged, key=lambda e: (e.start, e.course))

    def items(self) -> list[WorkItem]:
        return build_items(
            self.deadlines(),
            self.store.tasks(),
            self.store.deadline_states(),
            self.store.logged_minutes(),
        )

    def risks(self, items: list[WorkItem]) -> dict[str, Risk]:
        dues = [i.due for i in items if i.due and i.due > self.now()]
        horizon = max(dues).date() if dues else self.today()
        events = self.events(self.today(), horizon)
        return assess_risks(items, events, self.now(), self.settings)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    properties: JSON
    run: Callable[[ToolContext, JSON], JSON | Proposal]
    required: tuple[str, ...] = ()
    writes: bool = False

    def definition(self) -> JSON:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": self.properties,
                "required": list(self.required),
                "additionalProperties": False,
            },
        }


# --- argument helpers ---------------------------------------------------------------------


def _opt_str(args: JSON, key: str) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolError(f"'{key}' must be a string")
    return value.strip() or None


def _req_str(args: JSON, key: str) -> str:
    value = _opt_str(args, key)
    if value is None:
        raise ToolError(f"'{key}' is required")
    return value


def _opt_int(args: JSON, key: str, low: int, high: int) -> int | None:
    value = args.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ToolError(f"'{key}' must be a number")
    if not low <= value <= high:
        raise ToolError(f"'{key}' must be between {low} and {high}")
    return int(value)


def _day(args: JSON, key: str, default: date) -> date:
    try:
        return parse_day(_opt_str(args, key), default)
    except ValueError as exc:
        raise ToolError(str(exc)) from None


def _range(ctx: ToolContext, args: JSON, default_days: int) -> tuple[date, date]:
    start = _day(args, "from_date", ctx.today())
    end = _day(args, "to_date", start + timedelta(days=default_days))
    if end < start:
        raise ToolError("'to_date' is before 'from_date'")
    if (end - start).days > MAX_RANGE_DAYS:
        raise ToolError(f"ranges are limited to {MAX_RANGE_DAYS} days")
    return start, end


def _moment(value: str, key: str) -> datetime:
    try:
        return parse_moment(value)
    except ValueError as exc:
        raise ToolError(f"'{key}': {exc}") from None


def _course_filter(args: JSON) -> str | None:
    course = _opt_str(args, "course")
    return course.upper() if course else None


DATE_PROP = {"type": "string", "description": "ISO date, e.g. 2026-11-24, or 'today'/'tomorrow'"}
COURSE_PROP = {"type": "string", "description": "Course code filter, e.g. CS-E4780"}
ITEM_PROP = {
    "type": "string",
    "description": "Id of a deadline (D-xxxxxx) or one of my tasks (T-n), from a previous result",
}


# --- serialisation ------------------------------------------------------------------------


def item_dict(item: WorkItem, risk: Risk | None) -> JSON:
    data: JSON = {
        "id": item.id,
        "kind": item.kind,
        "course": item.course,
        "title": item.title,
        "category": item.category,
        "source": item.source,
        "estimate_min": item.estimate_min,
        "estimate_source": item.estimate_source,
        "logged_min": item.logged_min,
        "remaining_min": item.remaining_min,
    }
    if item.due:
        data["due"] = iso(item.due)
        data["due_local"] = fmt_moment(item.due)
    if risk:
        data["risk"] = risk.status
        data["risk_reason"] = risk.reason
    return data


def _suggestion_dict(s: Suggestion) -> JSON:
    data: JSON = {
        "item_id": s.item_id,
        "course": s.course,
        "title": s.title,
        "start": iso(s.start),
        "end": iso(s.end),
        "when": f"{fmt_moment(s.start)}-{s.end:%H:%M}",
        "minutes": s.minutes,
    }
    if s.due:
        data["for_due"] = fmt_moment(s.due)
    return data


def _risk_summary(items: list[WorkItem], risks: dict[str, Risk]) -> list[JSON]:
    flagged = [i for i in items if i.id in risks and risks[i.id].status in RISKY_ORDER]
    # Risky items always have a due time (overdue, at_risk and tight all need one).
    flagged.sort(
        key=lambda i: (RISKY_ORDER.index(risks[i.id].status), i.due.timestamp() if i.due else 0)
    )
    return [item_dict(i, risks[i.id]) for i in flagged]


# --- read-only tools ----------------------------------------------------------------------


def get_deadlines(ctx: ToolContext, args: JSON) -> JSON:
    start, end = _range(ctx, args, 14)
    course = _course_filter(args)
    include_done = bool(args.get("include_done", False))
    lo, _ = day_bounds(start)
    _, hi = day_bounds(end)
    items = ctx.items()
    risks = ctx.risks(items)
    open_ids = {i.id for i in items}
    states = ctx.store.deadline_states()
    by_id = {i.id: i for i in items}
    results: list[JSON] = []
    for deadline in ctx.deadlines():
        if not lo <= deadline.due < hi or (course and deadline.course != course):
            continue
        if deadline.id in open_ids:
            results.append(item_dict(by_id[deadline.id], risks.get(deadline.id)))
        elif include_done:
            state = states.get(deadline.id)
            done = {**deadline.to_dict(), "done": True}
            if state and state.done_at:
                done["done_at"] = fmt_moment(state.done_at)
            results.append(done)
    return {
        "range": {"from": start.isoformat(), "to": end.isoformat()},
        "now": fmt_moment(ctx.now()),
        "deadlines": results,
        "count": len(results),
        "note": "Deadlines marked done are hidden unless include_done is true.",
    }


def get_timetable(ctx: ToolContext, args: JSON) -> JSON:
    start, end = _range(ctx, args, 0)
    course = _course_filter(args)
    events = [e.to_dict() for e in ctx.events(start, end) if not course or e.course == course]
    return {"range": {"from": start.isoformat(), "to": end.isoformat()}, "events": events}


def list_tasks(ctx: ToolContext, args: JSON) -> JSON:
    course = _course_filter(args)
    include_done = bool(args.get("include_done", False))
    items = ctx.items()
    risks = ctx.risks(items)
    by_id = {i.id: i for i in items}
    tasks: list[JSON] = []
    for task in ctx.store.tasks(include_done=include_done):
        if course and (task.course or "").upper() != course:
            continue
        if task.ref in by_id:
            tasks.append(item_dict(by_id[task.ref], risks.get(task.ref)))
        else:
            tasks.append({"id": task.ref, "title": task.title, "course": task.course, "done": True})
    return {"tasks": tasks, "count": len(tasks)}


def _day_plan(ctx: ToolContext, day: date, items: list[WorkItem]) -> JSON:
    lo, hi = day_bounds(day)
    events = ctx.events(day, day)
    saved = ctx.store.blocks_between(lo, hi)
    future_saved = ctx.store.blocks_between(ctx.now(), hi + timedelta(days=60))
    suggestions = suggest_blocks([day], items, events, future_saved, ctx.now(), ctx.settings)
    return {
        "date": day.isoformat(),
        "day": fmt_day(day),
        "events": [e.to_dict() for e in events],
        "saved_blocks": [
            {
                "block_id": b.ref,
                "item_id": b.item_id,
                "course": b.course,
                "title": b.title,
                "when": f"{fmt_moment(b.start)}-{b.end:%H:%M}",
                "start": iso(b.start),
                "end": iso(b.end),
            }
            for b in saved
        ],
        "suggested_blocks": [_suggestion_dict(s) for s in suggestions],
    }


def plan_day(ctx: ToolContext, args: JSON) -> JSON:
    day = _day(args, "date", ctx.today())
    items = ctx.items()
    plan = _day_plan(ctx, day, items)
    plan["study_window"] = f"{ctx.settings.day_start:%H:%M}-{ctx.settings.day_end:%H:%M}"
    plan["at_risk"] = _risk_summary(items, ctx.risks(items))
    plan["note"] = "suggested_blocks are not saved. Use schedule_blocks to save them."
    return plan


def plan_week(ctx: ToolContext, args: JSON) -> JSON:
    start = _day(args, "start_date", ctx.today())
    items = ctx.items()
    days = [start + timedelta(days=n) for n in range(7)]
    events = ctx.events(days[0], days[-1])
    lo, _ = day_bounds(days[0])
    saved = ctx.store.blocks_between(max(lo, ctx.now()), lo + timedelta(days=60))
    suggestions = suggest_blocks(days, items, events, saved, ctx.now(), ctx.settings)
    per_day = []
    for day in days:
        per_day.append(
            {
                "date": day.isoformat(),
                "day": fmt_day(day),
                "events": [
                    f"{e.start:%H:%M}-{e.end:%H:%M} {e.course} {e.kind}"
                    for e in events
                    if e.start.date() == day
                ],
                "saved_blocks": [
                    f"{b.ref} {b.start:%H:%M}-{b.end:%H:%M} {b.course} {b.title}"
                    for b in saved
                    if b.start.date() == day
                ],
                "suggested_blocks": [
                    _suggestion_dict(s) for s in suggestions if s.start.date() == day
                ],
            }
        )
    totals: dict[str, int] = {}
    for s in suggestions:
        totals[s.item_id] = totals.get(s.item_id, 0) + s.minutes
    return {
        "range": {"from": days[0].isoformat(), "to": days[-1].isoformat()},
        "days": per_day,
        "suggested_minutes_per_item": totals,
        "at_risk": _risk_summary(items, ctx.risks(items)),
        "note": "suggested_blocks are not saved. Use schedule_blocks to save them.",
    }


def get_progress(ctx: ToolContext, args: JSON) -> JSON:
    start = _day(args, "from_date", ctx.today() - timedelta(days=7))
    end = _day(args, "to_date", ctx.today())
    lo, _ = day_bounds(start)
    _, hi = day_bounds(end)
    entries = [
        {
            "logged": fmt_moment(p.logged_at),
            "text": p.text,
            "item_id": p.item_id,
            "course": p.course,
            "minutes": p.minutes,
        }
        for p in ctx.store.progress_between(lo, hi)
    ]
    done = [
        {"id": t.ref, "title": t.title, "course": t.course, "done_at": fmt_moment(t.done_at)}
        for t in ctx.store.tasks(include_done=True)
        if t.done_at and lo <= t.done_at < hi
    ]
    return {
        "range": {"from": start.isoformat(), "to": end.isoformat()},
        "progress": entries,
        "tasks_completed": done,
    }


# --- write tools: each returns a Proposal; nothing is saved until I approve ------------------


def _find_item(ctx: ToolContext, item_id: str) -> WorkItem:
    for item in ctx.items():
        if item.id.upper() == item_id.upper():
            return item
    raise ToolError(f"no open deadline or task with id {item_id}; look it up first")


def add_task(ctx: ToolContext, args: JSON) -> Proposal:
    title = _req_str(args, "title")
    course = _course_filter(args)
    estimate = _opt_int(args, "estimate_min", 5, 6000)
    deadline_id = _opt_str(args, "deadline_id")
    due_text = _opt_str(args, "due")
    due: datetime | None = None
    if due_text:  # a bare date means 23:59 that day, like MyCourses all-day deadlines
        is_date = len(due_text) <= 10
        due = end_of_day(_day(args, "due", ctx.today())) if is_date else _moment(due_text, "due")
    if deadline_id:
        linked = _find_item(ctx, deadline_id)
        course = course or linked.course
    lines = [f"Title: {title}"]
    lines += [f"Course: {course}"] if course else []
    lines += [f"Due: {fmt_moment(due)}"] if due else []
    lines += [f"Estimate: {estimate} min"] if estimate else []
    lines += [f"Linked to deadline: {deadline_id}"] if deadline_id else []

    def apply() -> JSON:
        task_id = ctx.store.add_task(title, ctx.now(), course, due, estimate, deadline_id)
        return {"status": "applied", "task_id": f"T-{task_id}", "title": title}

    return Proposal("Add task", lines, apply)


def add_deadline(ctx: ToolContext, args: JSON) -> Proposal:
    try:
        course = courses.clean_code(_req_str(args, "course"))
        kind = courses.clean_kind(_opt_str(args, "kind") or "assignment")
        title = courses.clean_text(_req_str(args, "title"), "title")
        due = courses.parse_due(_req_str(args, "due"))
    except courses.CourseInputError as exc:
        raise ToolError(str(exc)) from None
    estimate = _opt_int(args, "estimate_min", 5, 6000)
    known = ctx.store.course(course)
    lines = [f"Course: {course}" + (f" {known.name}" if known else " (new course)")]
    lines += [f"{kind.capitalize()}: {title}", f"Due: {fmt_moment(due)}"]
    lines += [f"Estimate: {estimate} min"] if estimate else []

    def apply() -> JSON:
        if ctx.store.course(course) is None:
            ctx.store.add_course(course, course, ctx.now())
        new_id = ctx.store.add_course_deadline(course, kind, title, due, ctx.now())
        ref = f"M-{new_id}"
        if estimate:
            ctx.store.set_deadline_estimate(ref, estimate)
        return {"status": "applied", "deadline_id": ref, "title": title}

    return Proposal(f"Add {kind}", lines, apply)


def complete_item(ctx: ToolContext, args: JSON) -> Proposal:
    item = _find_item(ctx, _req_str(args, "item_id"))

    def apply() -> JSON:
        task_id = parse_ref(item.id, "T")
        if task_id is not None:
            ctx.store.complete_task(task_id, ctx.now())
        else:
            ctx.store.set_deadline_done(item.id, ctx.now())
        return {"status": "applied", "completed": item.id, "title": item.title}

    return Proposal("Mark as done", [f"{item.id} {item.course}: {item.title}"], apply)


def log_progress(ctx: ToolContext, args: JSON) -> Proposal:
    text = _req_str(args, "text")
    minutes = _opt_int(args, "minutes", 1, 1440)
    mark_done = bool(args.get("mark_done", False))
    item_id = _opt_str(args, "item_id")
    item = _find_item(ctx, item_id) if item_id else None
    lines = [f"Note: {text}"]
    if item:
        lines.append(f"For: {item.id} {item.course}: {item.title}")
    if minutes:
        lines.append(f"Time spent: {minutes} min")
    if mark_done and item:
        lines.append("Also mark it as done")

    def apply() -> JSON:
        ctx.store.log_progress(
            text, ctx.now(), item.id if item else None, item.course if item else None, minutes
        )
        result: JSON = {"status": "applied", "logged": text}
        if mark_done and item:
            task_id = parse_ref(item.id, "T")
            if task_id is not None:
                ctx.store.complete_task(task_id, ctx.now())
            else:
                ctx.store.set_deadline_done(item.id, ctx.now())
            result["completed"] = item.id
        return result

    return Proposal("Log progress", lines, apply)


def set_estimate(ctx: ToolContext, args: JSON) -> Proposal:
    item = _find_item(ctx, _req_str(args, "item_id"))
    minutes = _opt_int(args, "estimate_min", 5, 6000)
    if minutes is None:
        raise ToolError("'estimate_min' is required")

    def apply() -> JSON:
        task_id = parse_ref(item.id, "T")
        if task_id is not None:
            ctx.store.set_task_estimate(task_id, minutes)
        else:
            ctx.store.set_deadline_estimate(item.id, minutes)
        return {"status": "applied", "item_id": item.id, "estimate_min": minutes}

    return Proposal(
        "Change estimate",
        [f"{item.id} {item.course}: {item.title}", f"{item.estimate_min} -> {minutes} min"],
        apply,
    )


def _check_free(
    ctx: ToolContext,
    start: datetime,
    end: datetime,
    item_id: str,
    ignore_block: int | None = None,
) -> None:
    if start < ctx.now():
        raise ToolError(f"{fmt_moment(start)} is in the past")
    item = next((i for i in ctx.items() if i.id == item_id), None)
    if item and item.due and end > item.due:
        raise ToolError(
            f"a block ending {fmt_moment(end)} is after {item.course} {item.title} is due"
            f" ({fmt_moment(item.due)}), so it would not help"
        )
    for event in ctx.events(start.date(), end.date()):
        if event.start < end and start < event.end:
            raise ToolError(
                f"{fmt_moment(start)}-{end:%H:%M} overlaps {event.course} {event.kind}"
                f" {fmt_moment(event.start)}-{event.end:%H:%M}"
            )
    for block in ctx.store.blocks_between(start, end):
        if block.id != ignore_block:
            raise ToolError(f"overlaps saved block {block.ref} at {fmt_moment(block.start)}")


@dataclass(frozen=True)
class _NewBlock:
    item: WorkItem
    start: datetime
    end: datetime


def schedule_blocks(ctx: ToolContext, args: JSON) -> Proposal:
    raw = args.get("blocks")
    if not isinstance(raw, list) or not raw:
        raise ToolError("'blocks' must be a non-empty list")
    if len(raw) > 50:
        raise ToolError("at most 50 blocks at a time")
    planned: list[_NewBlock] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ToolError("each block needs item_id, start and minutes")
        item = _find_item(ctx, _req_str(entry, "item_id"))
        start = _moment(_req_str(entry, "start"), "start")
        minutes = _opt_int(entry, "minutes", 15, 480) or 60
        end = start + timedelta(minutes=minutes)
        _check_free(ctx, start, end, item.id)
        for other in planned:
            if other.start < end and start < other.end:
                raise ToolError(
                    f"blocks at {fmt_moment(start)} and {fmt_moment(other.start)}"
                    " overlap each other"
                )
        planned.append(_NewBlock(item, start, end))

    def apply() -> JSON:
        ids = [
            block_ref(
                ctx.store.add_block(
                    b.item.id, b.item.course, b.item.title, b.start, b.end, ctx.now()
                )
            )
            for b in planned
        ]
        return {"status": "applied", "saved_block_ids": ids}

    lines = [
        f"{fmt_moment(b.start)}-{b.end:%H:%M}  {b.item.course}: {b.item.title}" for b in planned
    ]
    return Proposal(f"Save {len(planned)} study block(s)", lines, apply)


def _saved_block(ctx: ToolContext, args: JSON) -> Block:
    ref = _req_str(args, "block_id")
    block_id = parse_ref(ref, "B")
    block = ctx.store.block(block_id) if block_id is not None else None
    if block is None:
        raise ToolError(f"no saved block {ref}; use plan_day or plan_week to see block ids")
    return block


def delete_study_block(ctx: ToolContext, args: JSON) -> Proposal:
    block = _saved_block(ctx, args)

    def apply() -> JSON:
        ctx.store.delete_block(block.id)
        return {"status": "applied", "deleted": block.ref}

    return Proposal(
        "Remove study block",
        [
            f"{block.ref} {block.course}: {block.title}",
            f"{fmt_moment(block.start)}-{block.end:%H:%M}",
        ],
        apply,
    )


def move_study_block(ctx: ToolContext, args: JSON) -> Proposal:
    block = _saved_block(ctx, args)
    new_start = _moment(_req_str(args, "new_start"), "new_start")
    minutes = _opt_int(args, "minutes", 15, 480) or minutes_between(block.start, block.end)
    new_end = new_start + timedelta(minutes=minutes)
    _check_free(ctx, new_start, new_end, block.item_id, ignore_block=block.id)

    def apply() -> JSON:
        ctx.store.move_block(block.id, new_start, new_end)
        return {
            "status": "applied",
            "block_id": block.ref,
            "now_at": f"{fmt_moment(new_start)}-{new_end:%H:%M}",
        }

    return Proposal(
        "Move study block",
        [
            f"{block.ref} {block.course}: {block.title}",
            f"From: {fmt_moment(block.start)}-{block.end:%H:%M}",
            f"To:   {fmt_moment(new_start)}-{new_end:%H:%M}",
        ],
        apply,
    )


# --- registry -----------------------------------------------------------------------------

TOOLS: tuple[Tool, ...] = (
    Tool(
        "get_deadlines",
        "List my course deadlines from the MyCourses calendar between two dates (default: today"
        " and the next 14 days), with my effort estimate, logged work and a risk status computed"
        " by an earliest-deadline-first capacity check. Use this for any question about what is"
        " due. Each deadline has an id, course, title, due_local and source.",
        {
            "from_date": DATE_PROP,
            "to_date": DATE_PROP,
            "course": COURSE_PROP,
            "include_done": {"type": "boolean", "description": "Also list deadlines I finished"},
        },
        get_deadlines,
    ),
    Tool(
        "get_timetable",
        "List my lectures, exercise sessions and other timetabled events between two dates"
        " (default: today only).",
        {"from_date": DATE_PROP, "to_date": DATE_PROP, "course": COURSE_PROP},
        get_timetable,
    ),
    Tool(
        "list_tasks",
        "List my own tasks (things I added myself, not calendar deadlines), with risk status.",
        {
            "course": COURSE_PROP,
            "include_done": {"type": "boolean", "description": "Also list finished tasks"},
        },
        list_tasks,
    ),
    Tool(
        "plan_day",
        "Plan one day: my timetable for that day, study blocks I already saved (with block ids),"
        " suggested new study blocks in free time (earliest deadline first, respecting my study"
        " window and daily cap), and everything currently at risk.",
        {"date": DATE_PROP},
        plan_day,
    ),
    Tool(
        "plan_week",
        "Plan 7 days starting at start_date (default today): events, saved blocks and suggested"
        " study blocks for each day, plus everything at risk.",
        {"start_date": DATE_PROP},
        plan_week,
    ),
    Tool(
        "get_progress",
        "What I logged as done or worked on between two dates (default: the last 7 days),"
        " including tasks I completed.",
        {"from_date": DATE_PROP, "to_date": DATE_PROP},
        get_progress,
    ),
    Tool(
        "add_task",
        "Propose adding one of my own tasks. I must approve it before it is saved.",
        {
            "title": {"type": "string"},
            "course": COURSE_PROP,
            "due": {"type": "string", "description": "ISO date or datetime in Helsinki time"},
            "estimate_min": {"type": "integer", "description": "Estimated effort in minutes"},
            "deadline_id": {"type": "string", "description": "Link to a deadline id (D-xxxxxx)"},
        },
        add_task,
        required=("title",),
        writes=True,
    ),
    Tool(
        "add_deadline",
        "Propose adding a deadline I tell you about (assignment, project, exam, quiz, lab,"
        " presentation, other) to one of my courses. I must approve it.",
        {
            "course": {"type": "string", "description": "Course code, e.g. CS-E4780"},
            "kind": {"type": "string", "enum": list(courses.KINDS)},
            "title": {"type": "string"},
            "due": {
                "type": "string",
                "description": "ISO date (means 23:59) or datetime, Helsinki",
            },
            "estimate_min": {"type": "integer", "description": "Estimated effort in minutes"},
        },
        add_deadline,
        required=("course", "title", "due"),
        writes=True,
    ),
    Tool(
        "complete_item",
        "Propose marking a deadline (D-xxxxxx) or task (T-n) as done. I must approve it.",
        {"item_id": ITEM_PROP},
        complete_item,
        required=("item_id",),
        writes=True,
    ),
    Tool(
        "log_progress",
        "Propose logging what I worked on, optionally for a specific item, with minutes spent"
        " (this reduces the remaining effort) and optionally marking it done. I must approve it.",
        {
            "text": {"type": "string", "description": "What I did, in my words"},
            "item_id": ITEM_PROP,
            "minutes": {"type": "integer"},
            "mark_done": {"type": "boolean"},
        },
        log_progress,
        required=("text",),
        writes=True,
    ),
    Tool(
        "set_estimate",
        "Propose changing my effort estimate for a deadline or task. I must approve it.",
        {"item_id": ITEM_PROP, "estimate_min": {"type": "integer"}},
        set_estimate,
        required=("item_id", "estimate_min"),
        writes=True,
    ),
    Tool(
        "schedule_blocks",
        "Propose saving study blocks to my plan (usually blocks from plan_day or plan_week)."
        " Blocks may not overlap events or other blocks. I must approve them.",
        {
            "blocks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "item_id": ITEM_PROP,
                        "start": {"type": "string", "description": "ISO datetime, Helsinki"},
                        "minutes": {"type": "integer"},
                    },
                    "required": ["item_id", "start", "minutes"],
                    "additionalProperties": False,
                },
            }
        },
        schedule_blocks,
        required=("blocks",),
        writes=True,
    ),
    Tool(
        "move_study_block",
        "Propose moving a saved study block (B-n) to a new start time. I must approve it.",
        {
            "block_id": {"type": "string", "description": "Saved block id, e.g. B-3"},
            "new_start": {"type": "string", "description": "ISO datetime, Helsinki"},
            "minutes": {"type": "integer", "description": "New length; default keeps it"},
        },
        move_study_block,
        required=("block_id", "new_start"),
        writes=True,
    ),
    Tool(
        "delete_study_block",
        "Propose removing a saved study block (B-n) from my plan. I must approve it.",
        {"block_id": {"type": "string", "description": "Saved block id, e.g. B-3"}},
        delete_study_block,
        required=("block_id",),
        writes=True,
    ),
)

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}
Approver = Callable[[Proposal], bool]


def tool_definitions() -> list[JSON]:
    """Tool schemas in a fixed order. A stable order keeps the prompt cache valid."""
    return [tool.definition() for tool in TOOLS]


@dataclass(frozen=True)
class ToolOutcome:
    content: str
    is_error: bool
    approved: bool | None
    latency_ms: int


def execute(name: str, args: JSON, ctx: ToolContext, approve: Approver) -> ToolOutcome:
    started = time.perf_counter()

    def done(payload: JSON, is_error: bool = False, approved: bool | None = None) -> ToolOutcome:
        elapsed = int((time.perf_counter() - started) * 1000)
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return ToolOutcome(text, is_error, approved, elapsed)

    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        return done({"error": f"unknown tool {name}"}, is_error=True)
    try:
        result = tool.run(ctx, args)
    except ToolError as exc:
        return done({"error": str(exc)}, is_error=True)
    except SourceError as exc:
        return done({"error": f"calendar unavailable: {exc}"}, is_error=True)
    if isinstance(result, Proposal):
        if not approve(result):
            return done(
                {
                    "status": "declined",
                    "message": "I declined this change. Nothing was saved. Do not retry it"
                    " unless I ask again.",
                },
                approved=False,
            )
        applied = result.apply()
        applied["change"] = result.lines  # echo what I approved, so the answer can cite it
        return done(applied, approved=True)
    return done(result)
