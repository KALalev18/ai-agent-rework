from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from study_agent.demo import DEADLINES, TIMETABLE
from study_agent.sources.ics import IcsError, classify_event, parse_deadlines, parse_events
from study_agent.timeutil import TZ


def ics(*events: str) -> bytes:
    body = "".join(f"BEGIN:VEVENT\r\n{e.strip()}\r\nEND:VEVENT\r\n" for e in events)
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:test\r\n{body}END:VCALENDAR\r\n".encode()


def event(uid: str, summary: str, start: str, extra: str = "") -> str:
    lines = [f"UID:{uid}", f"SUMMARY:{summary}", start]
    if extra:
        lines.append(extra)
    return "\r\n".join(lines)


def test_demo_calendar_skips_opens_events_and_strips_due_suffix() -> None:
    deadlines = parse_deadlines(DEADLINES.read_bytes(), "mycourses-demo.ics")
    titles = [d.title for d in deadlines]
    assert "Quiz 5 opens" not in titles
    assert "Quiz 5" in titles
    assert "Assignment 3: Tool-calling agent" in titles
    assert all(d.source == "mycourses-demo.ics" for d in deadlines)
    assert len(deadlines) == 10


def test_utc_due_time_becomes_2359_helsinki_in_winter() -> None:
    [d] = parse_deadlines(ics(event("1", "A1 is due", "DTSTART:20261125T215900Z")), "x.ics")
    assert d.due == datetime(2026, 11, 25, 23, 59, tzinfo=TZ)
    assert d.due.utcoffset() == timedelta(hours=2)


def test_2359_deadlines_on_both_sides_of_the_october_dst_change() -> None:
    # DST ends Sun 25 Oct 2026 at 04:00 local: UTC+3 before, UTC+2 after.
    data = ics(
        event("before", "Before is due", "DTSTART:20261024T205900Z"),
        event("after", "After is due", "DTSTART:20261025T215900Z"),
    )
    before, after = parse_deadlines(data, "x.ics")
    assert (before.due.hour, before.due.minute) == (23, 59)
    assert before.due.utcoffset() == timedelta(hours=3)
    assert (after.due.hour, after.due.minute) == (23, 59)
    assert after.due.utcoffset() == timedelta(hours=2)


def test_2359_deadline_around_the_march_dst_change() -> None:
    # DST starts Sun 29 Mar 2026: 23:59 that day is UTC+3, so 20:59Z.
    [d] = parse_deadlines(ics(event("m", "Spring is due", "DTSTART:20260329T205900Z")), "x.ics")
    assert d.due == datetime(2026, 3, 29, 23, 59, tzinfo=TZ)
    assert d.due.utcoffset() == timedelta(hours=3)


def test_all_day_deadline_means_end_of_that_day() -> None:
    [d] = parse_deadlines(ics(event("d", "Peer review closes", "DTSTART;VALUE=DATE:20261201")), "x")
    assert d.due == datetime(2026, 12, 1, 23, 59, tzinfo=TZ)
    assert d.title == "Peer review"


def test_floating_time_is_helsinki_and_tzid_is_respected() -> None:
    data = ics(
        event("f", "Floating is due", "DTSTART:20261201T120000"),
        event("z", "Berlin is due", "DTSTART;TZID=Europe/Berlin:20261201T120000"),
    )
    floating, berlin = parse_deadlines(data, "x")
    assert floating.due == datetime(2026, 12, 1, 12, 0, tzinfo=TZ)
    assert berlin.due == datetime(2026, 12, 1, 13, 0, tzinfo=TZ)


def test_course_code_from_categories_then_summary() -> None:
    data = ics(
        event("c1", "Report is due", "DTSTART:20261201T100000Z", "CATEGORIES:CS-E4780 Systems"),
        event("c2", "MS-A0111: Round 3 is due", "DTSTART:20261202T100000Z"),
        event("c3", "Something is due", "DTSTART:20261203T100000Z"),
    )
    a, b, c = parse_deadlines(data, "x")
    assert (a.course, a.title) == ("CS-E4780", "Report")
    assert (b.course, b.title) == ("MS-A0111", "Round 3")
    assert c.course == "UNKNOWN"


def test_ids_are_stable_and_description_is_never_used() -> None:
    first = parse_deadlines(DEADLINES.read_bytes(), "x")
    second = parse_deadlines(DEADLINES.read_bytes(), "x")
    assert [d.id for d in first] == [d.id for d in second]
    assert all("never read" not in str(d.to_dict()) for d in first)


def test_invalid_calendar_raises() -> None:
    with pytest.raises(IcsError):
        parse_deadlines(b"this is not a calendar", "x")


def test_recurring_lecture_stays_at_1015_local_across_dst() -> None:
    events = parse_events(TIMETABLE.read_bytes(), "t", date(2026, 10, 19), date(2026, 10, 26))
    lectures = [e for e in events if e.course == "CS-E9001" and e.start.weekday() == 0]
    assert [e.start.date() for e in lectures] == [date(2026, 10, 19), date(2026, 10, 26)]
    assert all((e.start.hour, e.start.minute) == (10, 15) for e in lectures)
    assert lectures[0].start.utcoffset() != lectures[1].start.utcoffset()


def test_timetable_week_contents() -> None:
    events = parse_events(TIMETABLE.read_bytes(), "t", date(2026, 11, 23), date(2026, 11, 29))
    assert len(events) == 7  # Mon 2, Tue 2, Wed 1, Thu 1, Fri 1
    assert {e.kind for e in events} == {"lecture", "exercise"}
    assert not [e for e in events if e.start.date() > date(2026, 11, 27)]


def test_classify_event() -> None:
    assert classify_event("CS-E4780 Lecture") == "lecture"
    assert classify_event("Harjoitus 3") == "exercise"
    assert classify_event("Final exam") == "exam"
    assert classify_event("Guild sauna") == "event"
