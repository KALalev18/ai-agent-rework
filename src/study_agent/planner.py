"""Deterministic planning: risk assessment and study-block suggestions.

The model never does arithmetic on my calendar. These functions do, and the model explains the
result. That keeps the numbers testable and the same on every run.

Risk uses the earliest-deadline-first (EDF) feasibility test: sort open work by due time, and for
each item compare the work needed up to and including it with the free study time before it is
due. EDF is optimal for this single-worker case: if work cannot fit in EDF order, it cannot fit in
any order, so "at risk" is a real warning, not a guess.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from study_agent.config import Settings
from study_agent.models import Deadline, Event
from study_agent.store import Block, DeadlineState, Task
from study_agent.timeutil import at, ceil_to_quarter, fmt_moment, minutes_between

# Default effort estimates by keyword in the title, in minutes. I override them with set_estimate.
DEFAULT_ESTIMATES: tuple[tuple[tuple[str, ...], int], ...] = (
    (("exam", "tentti"), 600),
    (("project", "milestone"), 900),
    (("report", "essay", "thesis"), 480),
    (("quiz",), 60),
    (("exercise", "assignment", "round", "homework", "lab"), 240),
)
DEFAULT_ESTIMATE_MIN = 180
MIN_BLOCK_MIN = 30
BREAK_MIN = 15
TIGHT_RATIO = 0.75
RISKY_ORDER = ("overdue", "at_risk", "tight")  # most urgent first

Interval = tuple[datetime, datetime]


def default_estimate(title: str) -> int:
    lowered = title.lower()
    for words, minutes in DEFAULT_ESTIMATES:
        if any(word in lowered for word in words):
            return minutes
    return DEFAULT_ESTIMATE_MIN


@dataclass(frozen=True)
class WorkItem:
    id: str
    kind: str  # "deadline" or "task"
    course: str
    title: str
    due: datetime | None
    estimate_min: int
    estimate_source: str  # "default" or "mine"
    logged_min: int
    source: str
    category: str = "deadline"

    @property
    def remaining_min(self) -> int:
        return max(0, self.estimate_min - self.logged_min)


@dataclass(frozen=True)
class Risk:
    status: str  # overdue | at_risk | tight | ok | done_enough | no_due
    needed_min: int
    capacity_min: int
    reason: str


@dataclass(frozen=True)
class Suggestion:
    item_id: str
    course: str
    title: str
    start: datetime
    end: datetime
    due: datetime | None

    @property
    def minutes(self) -> int:
        return minutes_between(self.start, self.end)


def build_items(
    deadlines: Sequence[Deadline],
    tasks: Sequence[Task],
    states: dict[str, DeadlineState],
    logged: dict[str, int],
) -> list[WorkItem]:
    """Open work: deadlines I have not marked done, plus my own open tasks."""
    by_id = {d.id: d for d in deadlines}
    items: list[WorkItem] = []
    for deadline in deadlines:
        state = states.get(deadline.id)
        if state and state.done_at:
            continue
        mine = state.estimate_min if state else None
        items.append(
            WorkItem(
                deadline.id,
                "deadline",
                deadline.course,
                deadline.title,
                deadline.due,
                mine
                if mine is not None
                else default_estimate(f"{deadline.category} {deadline.title}"),
                "mine" if mine is not None else "default",
                logged.get(deadline.id, 0),
                deadline.source,
                deadline.category,
            )
        )
    for task in tasks:
        if task.done_at:
            continue
        linked = by_id.get(task.deadline_id or "")
        due = task.due or (linked.due if linked else None)
        course = task.course or (linked.course if linked else "") or "-"
        items.append(
            WorkItem(
                task.ref,
                "task",
                course,
                task.title,
                due,
                task.estimate_min if task.estimate_min is not None else 60,
                "mine" if task.estimate_min is not None else "default",
                logged.get(task.ref, 0),
                "my tasks",
                "task",
            )
        )
    return items


def _subtract(window: Interval, busy: Iterable[Interval]) -> list[Interval]:
    free = [window]
    for b_start, b_end in sorted(busy):
        next_free: list[Interval] = []
        for f_start, f_end in free:
            if b_end <= f_start or b_start >= f_end:
                next_free.append((f_start, f_end))
                continue
            if b_start > f_start:
                next_free.append((f_start, b_start))
            if b_end < f_end:
                next_free.append((b_end, f_end))
        free = next_free
    return [(s, e) for s, e in free if minutes_between(s, e) >= BREAK_MIN]


def free_intervals(
    day: date, busy: Iterable[Interval], now: datetime, settings: Settings
) -> list[Interval]:
    """Free time inside my study window on ``day``, after ``now`` and outside ``busy``."""
    start, end = at(day, settings.day_start), at(day, settings.day_end)
    start = max(start, ceil_to_quarter(now))
    if start >= end:
        return []
    return _subtract((start, end), busy)


def _busy(events: Iterable[Event], blocks: Iterable[Block] = ()) -> list[Interval]:
    return [(e.start, e.end) for e in events] + [(b.start, b.end) for b in blocks]


def capacity_until(until: datetime, events: Sequence[Event], now: datetime, s: Settings) -> int:
    """Study minutes available from now until ``until``, honouring the daily cap."""
    total, day, busy = 0, now.date(), _busy(events)
    while day <= until.date():
        free = sum(
            minutes_between(start, min(end, until))
            for start, end in free_intervals(day, busy, now, s)
            if start < until
        )
        total += min(free, s.daily_study_cap_min)
        day += timedelta(days=1)
    return total


def assess_risks(
    items: Sequence[WorkItem], events: Sequence[Event], now: datetime, settings: Settings
) -> dict[str, Risk]:
    risks: dict[str, Risk] = {}
    cumulative = 0
    for item in sorted((i for i in items if i.due), key=lambda i: (i.due, i.id)):
        assert item.due is not None
        if item.due <= now:
            risks[item.id] = Risk("overdue", item.remaining_min, 0, "the due time has passed")
            continue
        if item.remaining_min == 0:
            risks[item.id] = Risk("done_enough", 0, 0, "logged work covers the estimate")
            continue
        cumulative += item.remaining_min
        capacity = capacity_until(item.due, events, now, settings)
        ratio = cumulative / capacity if capacity else float("inf")
        status = "at_risk" if ratio > 1 else "tight" if ratio > TIGHT_RATIO else "ok"
        reason = (
            f"needs {cumulative / 60:.1f} h of work up to this deadline (earlier deadlines"
            f" included), {capacity / 60:.1f} h of free study time before {fmt_moment(item.due)}"
        )
        risks[item.id] = Risk(status, cumulative, capacity, reason)
    for item in items:
        if item.due is None:
            risks[item.id] = Risk("no_due", item.remaining_min, 0, "no due date")
    return risks


def suggest_blocks(
    days: Sequence[date],
    items: Sequence[WorkItem],
    events: Sequence[Event],
    saved: Sequence[Block],
    now: datetime,
    settings: Settings,
) -> list[Suggestion]:
    """Fill free time with study blocks, always working on the earliest-due unfinished item.

    Blocks I already saved count as busy time and as work already scheduled for their item.
    """
    remaining = {item.id: item.remaining_min for item in items}
    for block in saved:
        if block.start >= now and block.item_id in remaining:
            remaining[block.item_id] -= minutes_between(block.start, block.end)
    far_future = now + timedelta(days=3650)
    queue = sorted(items, key=lambda i: (i.due or far_future, i.id))
    busy = _busy(events, saved)
    suggestions: list[Suggestion] = []
    for day in days:
        used = sum(minutes_between(b.start, b.end) for b in saved if b.start.date() == day)
        for slot_start, slot_end in free_intervals(day, busy, now, settings):
            cursor = slot_start
            while True:
                room = min(
                    minutes_between(cursor, slot_end),
                    settings.daily_study_cap_min - used,
                    settings.max_block_min,
                )
                if room < MIN_BLOCK_MIN:
                    break
                item = next(
                    (
                        i
                        for i in queue
                        if remaining[i.id] > 0
                        and (i.due is None or i.due >= cursor + timedelta(minutes=MIN_BLOCK_MIN))
                    ),
                    None,
                )
                if item is None:
                    break
                length = min(room, remaining[item.id])
                if item.due is not None:
                    length = min(length, minutes_between(cursor, item.due))
                length = max(MIN_BLOCK_MIN, length - length % 15)
                end = cursor + timedelta(minutes=length)
                suggestions.append(
                    Suggestion(item.id, item.course, item.title, cursor, end, item.due)
                )
                remaining[item.id] -= length
                used += length
                cursor = end + timedelta(minutes=BREAK_MIN)
    return suggestions
