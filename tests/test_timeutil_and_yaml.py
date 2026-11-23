from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from study_agent.sources.timetable_yaml import TimetableError, parse_timetable_yaml
from study_agent.timeutil import (
    TZ,
    ceil_to_quarter,
    fmt_moment,
    parse_day,
    parse_moment,
    to_local,
)

TODAY = date(2026, 11, 23)


def test_parse_day_accepts_iso_and_relative_words() -> None:
    assert parse_day(None, TODAY) == TODAY
    assert parse_day("tomorrow", TODAY) == date(2026, 11, 24)
    assert parse_day("yesterday", TODAY) == date(2026, 11, 22)
    assert parse_day("2026-12-01T10:00", TODAY) == date(2026, 12, 1)
    with pytest.raises(ValueError):
        parse_day("next tuesday", TODAY)


def test_parse_moment_treats_missing_offset_as_helsinki() -> None:
    moment = parse_moment("2026-11-29T13:00")
    assert moment == datetime(2026, 11, 29, 13, 0, tzinfo=TZ)
    assert parse_moment("2026-11-29T11:00Z") == moment


def test_to_local_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError):
        to_local(datetime(2026, 1, 1))


def test_formatting_and_rounding() -> None:
    moment = datetime(2026, 11, 27, 23, 59, tzinfo=TZ)
    assert fmt_moment(moment) == "Fri 27 Nov 23:59"
    assert ceil_to_quarter(datetime(2026, 11, 23, 8, 1, tzinfo=TZ)).minute == 15
    assert ceil_to_quarter(datetime(2026, 11, 23, 8, 0, tzinfo=TZ)).minute == 0


YAML = """
- course: CS-E4780
  kind: lecture
  weekday: Mon
  start: "10:15"
  end: "12:00"
  from: 2026-10-01
  until: 2026-11-30
- course: MS-A0111
  kind: exercise
  weekday: thu
  start: "14:15"
  end: "16:00"
"""


def test_yaml_timetable_expands_weekly_entries_across_dst() -> None:
    events = parse_timetable_yaml(YAML, "timetable.yaml", date(2026, 10, 19), date(2026, 10, 30))
    mondays = [e for e in events if e.course == "CS-E4780"]
    assert [e.start.date() for e in mondays] == [date(2026, 10, 19), date(2026, 10, 26)]
    assert mondays[0].start.utcoffset() == timedelta(hours=3)
    assert mondays[1].start.utcoffset() == timedelta(hours=2)
    assert all(e.start.hour == 10 for e in mondays)
    assert len([e for e in events if e.course == "MS-A0111"]) == 2


@pytest.mark.parametrize(
    "bad",
    [
        "just a string",
        "- course: X\n  weekday: mon\n  start: '10'\n  end: '11:00'",
        "- course: X\n  start: '10:00'\n  end: '11:00'",
    ],
)
def test_bad_yaml_timetable_raises_clear_error(bad: str) -> None:
    with pytest.raises(TimetableError):
        parse_timetable_yaml(bad, "t.yaml", TODAY, TODAY + timedelta(days=7))
