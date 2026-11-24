"""Parse .ics calendars into Deadline and Event objects.

MyCourses (Moodle) exports each deadline as a VEVENT whose DTSTART is the due time, usually in
UTC (e.g. 21:59Z, which is 23:59 in Helsinki during winter time). It also exports "... opens"
events, which are not deadlines and are skipped. DESCRIPTION is never read: it can contain
course text that has no reason to leave my machine.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

import icalendar
import recurring_ical_events

from study_agent.models import Deadline, Event, find_course_code, infer_category, short_id
from study_agent.timeutil import TZ, end_of_day, to_local

_OPENS_RE = re.compile(r"\bopens?\s*$", re.IGNORECASE)
_DUE_SUFFIX_RE = re.compile(r"\s+(is due|closes|due)\s*$", re.IGNORECASE)


class IcsError(Exception):
    """The calendar file could not be parsed."""


def _load(data: bytes) -> Any:
    try:
        return icalendar.Calendar.from_ical(data)
    except ValueError as exc:
        raise IcsError("not a valid .ics calendar") from exc


def _as_local(value: date | datetime) -> datetime:
    """DATE -> 23:59 that day; floating time -> Helsinki; aware -> converted to Helsinki."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=TZ)
        return to_local(value)
    return end_of_day(value)


def _text(component: Any, key: str) -> str:
    value = component.get(key)
    return str(value).strip() if value is not None else ""


def _categories(component: Any) -> list[str]:
    raw = component.get("CATEGORIES")
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    names: list[str] = []
    for item in items:
        names.extend(str(cat) for cat in getattr(item, "cats", [item]))
    return names


def parse_deadlines(data: bytes, source: str) -> list[Deadline]:
    calendar = _load(data)
    deadlines: list[Deadline] = []
    for component in calendar.walk("VEVENT"):
        summary = _text(component, "SUMMARY")
        start = component.get("DTSTART")
        if not summary or start is None or _OPENS_RE.search(summary):
            continue
        due = _as_local(start.dt)
        course = find_course_code(*_categories(component), summary) or "UNKNOWN"
        title = _DUE_SUFFIX_RE.sub("", summary)
        # Drop a leading "CS-E4780: " from the title when the course code is already known.
        title = re.sub(rf"^{re.escape(course)}\s*[:\-]\s*", "", title)
        uid = _text(component, "UID") or f"{summary}|{due.isoformat()}"
        deadlines.append(
            Deadline(short_id("D", uid), course, title, due, source, infer_category(title))
        )
    deadlines.sort(key=lambda d: (d.due, d.course, d.title))
    return deadlines


def classify_event(summary: str) -> str:
    lowered = summary.lower()
    for kind, words in (
        ("exam", ("exam", "tentti")),
        ("exercise", ("exercise", "harjoitus", "lab", "tutorial")),
        ("lecture", ("lecture", "luento")),
    ):
        if any(word in lowered for word in words):
            return kind
    return "event"


def parse_events(data: bytes, source: str, start: date, end: date) -> list[Event]:
    """Expand recurring events (RRULE) between ``start`` and ``end`` (inclusive)."""
    calendar = _load(data)
    window_start = datetime.combine(start, datetime.min.time(), tzinfo=TZ)
    window_end = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=TZ)
    events: list[Event] = []
    for component in recurring_ical_events.of(calendar).between(window_start, window_end):
        summary = _text(component, "SUMMARY")
        begin = component.get("DTSTART")
        if begin is None or not isinstance(begin.dt, datetime):
            continue  # all-day entries (holidays etc.) do not block study time
        finish = component.get("DTEND")
        begin_local = _as_local(begin.dt)
        end_local = _as_local(finish.dt) if finish is not None else begin_local + timedelta(hours=1)
        course = find_course_code(*_categories(component), summary) or "OTHER"
        events.append(
            Event(course, classify_event(summary), summary, begin_local, end_local, source)
        )
    events.sort(key=lambda e: (e.start, e.course))
    return events
