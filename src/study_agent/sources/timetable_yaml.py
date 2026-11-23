"""A hand-written weekly timetable, for courses whose schedule is not in any .ics export.

Format (one entry per weekly slot):

    - course: CS-E4780
      kind: lecture
      title: Scalable Systems lecture   # optional
      weekday: mon
      start: "10:15"
      end: "12:00"
      from: 2026-09-07
      until: 2026-12-11
"""

from __future__ import annotations

from datetime import date, time, timedelta
from typing import Any

import yaml

from study_agent.models import Event
from study_agent.timeutil import at

WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


class TimetableError(Exception):
    """The YAML timetable is malformed."""


def _time(value: Any, where: str) -> time:
    try:
        hours, minutes = str(value).split(":")
        return time(int(hours), int(minutes))
    except ValueError as exc:
        raise TimetableError(f"{where}: times must look like 10:15") from exc


def _date(value: Any, where: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise TimetableError(f"{where}: dates must look like 2026-09-07") from exc


def parse_timetable_yaml(text: str, source: str, start: date, end: date) -> list[Event]:
    entries = yaml.safe_load(text) or []
    if not isinstance(entries, list):
        raise TimetableError("the timetable must be a list of weekly entries")
    events: list[Event] = []
    for index, entry in enumerate(entries, start=1):
        where = f"entry {index}"
        if not isinstance(entry, dict):
            raise TimetableError(f"{where}: expected a mapping")
        try:
            weekday = WEEKDAYS[str(entry["weekday"]).lower()[:3]]
            course = str(entry["course"])
            begin, finish = _time(entry["start"], where), _time(entry["end"], where)
        except KeyError as exc:
            raise TimetableError(f"{where}: missing or invalid field {exc}") from exc
        kind = str(entry.get("kind", "lecture"))
        title = str(entry.get("title", f"{course} {kind}"))
        first = max(_date(entry.get("from", start), where), start)
        last = min(_date(entry.get("until", end), where), end)
        day = first + timedelta(days=(weekday - first.weekday()) % 7)
        while day <= last:
            events.append(Event(course, kind, title, at(day, begin), at(day, finish), source))
            day += timedelta(days=7)
    events.sort(key=lambda e: (e.start, e.course))
    return events
