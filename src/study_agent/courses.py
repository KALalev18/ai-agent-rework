"""Courses I type in myself: deadlines (assignments, projects, exams, ...) and weekly classes.

They are converted into the same Deadline and Event objects the .ics parser produces, so the
planner, risk check, tools, agent and grounding checker treat them exactly like imported data.
Imported calendars and typed-in courses can be used together.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from study_agent.models import Deadline, Event
from study_agent.store import Store
from study_agent.timeutil import at, end_of_day, parse_moment

SOURCE = "my courses"
KINDS = ("assignment", "project", "exam", "quiz", "lab", "presentation", "other")
SESSION_KINDS = ("lecture", "exercise", "lab", "seminar", "exam", "other")
CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-]{1,19}$")


class CourseInputError(ValueError):
    """Invalid course data from a form or a tool call. The message is shown to the user."""


def clean_code(raw: str) -> str:
    code = raw.strip().upper().replace(" ", "-")
    if not CODE_RE.match(code):
        raise CourseInputError("course codes use letters, digits and '-', e.g. CS-E4780")
    return code


def clean_text(raw: str, field: str, limit: int = 200) -> str:
    text = " ".join(raw.split())
    if not text:
        raise CourseInputError(f"{field} is required")
    if len(text) > limit:
        raise CourseInputError(f"{field} is longer than {limit} characters")
    return text


def clean_kind(raw: str, allowed: tuple[str, ...] = KINDS) -> str:
    kind = raw.strip().lower()
    if kind not in allowed:
        raise CourseInputError(f"type must be one of: {', '.join(allowed)}")
    return kind


def parse_due(raw: str) -> datetime:
    """'2026-12-14' means 23:59 that day; '2026-12-14T09:00' is Helsinki local time."""
    text = raw.strip()
    try:
        if len(text) <= 10:
            return end_of_day(date.fromisoformat(text))
        return parse_moment(text)
    except ValueError:
        raise CourseInputError("due must look like 2026-12-14 or 2026-12-14T09:00") from None


def deadlines(store: Store) -> list[Deadline]:
    names = {c.code: c.name for c in store.courses()}
    return [
        Deadline(
            id=d.ref,
            course=d.course,
            title=d.title,
            due=d.due,
            source=SOURCE,
            category=d.kind,
            course_name=names.get(d.course, ""),
        )
        for d in store.course_deadlines()
    ]


def events(store: Store, start: date, end: date) -> list[Event]:
    """Expand weekly classes into concrete events between start and end (inclusive)."""
    names = {c.code: c.name for c in store.courses()}
    result: list[Event] = []
    for s in store.sessions():
        first = max(s.first_day, start)
        last = min(s.last_day, end)
        day = first + timedelta(days=(s.weekday - first.weekday()) % 7)
        title = f"{s.course} {names.get(s.course, '')} {s.kind}".replace("  ", " ")
        while day <= last:
            result.append(Event(s.course, s.kind, title, at(day, s.start), at(day, s.end), SOURCE))
            day += timedelta(days=7)
    return result
