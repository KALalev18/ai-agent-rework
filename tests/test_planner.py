from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

from study_agent.config import Settings
from study_agent.models import Event
from study_agent.planner import (
    WorkItem,
    assess_risks,
    capacity_until,
    default_estimate,
    free_intervals,
    suggest_blocks,
)
from study_agent.store import Block
from study_agent.timeutil import TZ

MON = date(2026, 11, 23)
NOW = datetime(2026, 11, 23, 8, 0, tzinfo=TZ)
S = Settings(data_dir=Path())


def t(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)


def item(id_: str, due: datetime | None, minutes: int, logged: int = 0) -> WorkItem:
    return WorkItem(id_, "deadline", "CS-E1", id_, due, minutes, "default", logged, "x.ics")


def lecture(start: datetime, end: datetime) -> Event:
    return Event("CS-E1", "lecture", "Lecture", start, end, "t.ics")


def test_default_estimates_by_keyword() -> None:
    assert default_estimate("Final exam") == 600
    assert default_estimate("Quiz 4 closes") == 60
    assert default_estimate("Exercise round 3") == 240
    assert default_estimate("Something else") == 180


def test_free_intervals_subtract_events_and_respect_window_and_now() -> None:
    free = free_intervals(MON, [(t(23, 10, 15), t(23, 12))], t(23, 8, 50), S)
    assert free == [(t(23, 9), t(23, 10, 15)), (t(23, 12), t(23, 21))]
    later = free_intervals(MON, [], t(23, 13, 2), S)
    assert later == [(t(23, 13, 15), t(23, 21))]
    assert free_intervals(MON, [], t(23, 22), S) == []


def test_capacity_honours_daily_cap() -> None:
    # Mon 08:00 to Wed 23:59: three full days, each capped at 300 min.
    assert capacity_until(t(25, 23, 59), [], NOW, S) == 900
    small_cap = replace(S, daily_study_cap_min=60)
    assert capacity_until(t(25, 23, 59), [], NOW, small_cap) == 180


def test_risk_uses_cumulative_edf_demand() -> None:
    items = [item("first", t(24, 23, 59), 500), item("second", t(25, 23, 59), 500)]
    risks = assess_risks(items, [], NOW, S)
    # first: 500 needed vs 600 free (2 days x 300) -> tight; second: 1000 vs 900 -> at risk.
    assert risks["first"].status == "tight"
    assert risks["second"].status == "at_risk"
    assert "16.7 h" in risks["second"].reason


def test_risk_overdue_done_enough_and_no_due() -> None:
    items = [
        item("late", t(20, 12), 120),
        item("covered", t(27, 12), 120, logged=120),
        replace(item("free", None, 60), kind="task"),
        item("easy", t(30, 12), 60),
    ]
    risks = assess_risks(items, [], NOW, S)
    assert risks["late"].status == "overdue"
    assert risks["covered"].status == "done_enough"
    assert risks["free"].status == "no_due"
    assert risks["easy"].status == "ok"


def test_each_assignment_is_one_block_placed_earliest_deadline_first() -> None:
    items = [item("later", t(27, 12), 120), item("sooner", t(24, 12), 120)]
    events = [lecture(t(23, 10), t(23, 12))]
    blocks = suggest_blocks([MON], items, events, [], NOW, S)
    assert [(b.item_id, b.start, b.end) for b in blocks] == [
        ("sooner", t(23, 12), t(23, 14)),  # 09:00-10:00 is too short for all 2 h, so 12:00
        ("later", t(23, 14, 15), t(23, 16, 15)),  # one block each, 15 min break between
    ]


def test_big_items_spread_over_days_one_block_per_day() -> None:
    days = [MON, MON + timedelta(days=1), MON + timedelta(days=2)]
    blocks = suggest_blocks(days, [item("project", t(30, 12), 600)], [], [], NOW, S)
    assert [b.start.date() for b in blocks] == days  # never two blocks of it on one day
    assert all(b.minutes == S.max_block_min for b in blocks[:2])
    assert sum(b.minutes for b in blocks) == 600


def test_no_second_block_on_a_day_that_already_has_a_saved_one() -> None:
    saved = [Block(1, "a", "CS-E1", "a", t(23, 9), t(23, 10))]
    blocks = suggest_blocks([MON], [item("a", t(27, 12), 240)], [], saved, NOW, S)
    assert blocks == []


def test_suggestions_respect_cap_due_time_and_saved_blocks() -> None:
    items = [item("big", t(30, 12), 2000), item("due-today", t(23, 10), 600)]
    blocks = suggest_blocks([MON], items, [], [], NOW, S)
    assert sum(b.minutes for b in blocks) <= S.daily_study_cap_min
    assert all(b.end <= t(23, 10) for b in blocks if b.item_id == "due-today")

    saved = [Block(1, "small", "CS-E1", "small", t(24, 9), t(24, 10))]
    rest = suggest_blocks([MON], [item("small", t(26, 12), 60)], [], saved, NOW, S)
    assert rest == []  # the saved block already covers all remaining work


def test_no_suggestions_after_items_are_due() -> None:
    items = [item("gone", NOW + timedelta(minutes=20), 120)]
    assert suggest_blocks([MON], items, [], [], NOW, S) == []
