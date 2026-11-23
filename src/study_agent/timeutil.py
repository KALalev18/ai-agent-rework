"""Time handling. Everything the user sees is in Europe/Helsinki, including across DST changes.

Rule: store and compare aware datetimes only. Naive datetimes are a bug, except ICS "floating"
times, which are interpreted as Helsinki local time when parsed.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Helsinki")

Clock = Callable[[], datetime]


def system_clock() -> datetime:
    return datetime.now(TZ)


def fixed_clock(moment: datetime) -> Clock:
    """A clock frozen at ``moment``: used by the demo, replay mode and tests."""
    frozen = to_local(moment)
    return lambda: frozen


def to_local(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("naive datetime: attach a timezone before converting")
    return moment.astimezone(TZ)


def at(day: date, clock_time: time) -> datetime:
    """Local wall-clock time on ``day``. zoneinfo resolves the correct UTC offset for that date."""
    return datetime.combine(day, clock_time, tzinfo=TZ)


def end_of_day(day: date) -> datetime:
    return at(day, time(23, 59))


def day_bounds(day: date) -> tuple[datetime, datetime]:
    return at(day, time(0, 0)), at(day + timedelta(days=1), time(0, 0))


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def parse_day(value: str | None, today: date) -> date:
    """Parse a tool argument date. Accepts ISO dates plus 'today' / 'tomorrow' / 'yesterday'."""
    if value is None or value.strip() == "":
        return today
    text = value.strip().lower()
    relative = {"today": 0, "tomorrow": 1, "yesterday": -1}
    if text in relative:
        return today + timedelta(days=relative[text])
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"expected a date like 2026-11-24, got {value!r}") from exc


def parse_moment(value: str) -> datetime:
    """Parse an ISO datetime from a tool argument; a missing offset means Helsinki local time."""
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"expected a datetime like 2026-11-24T14:00, got {value!r}") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=TZ)
    return to_local(moment)


def fmt_day(day: date) -> str:
    return f"{day:%a} {day.day} {day:%b}"


def fmt_moment(moment: datetime) -> str:
    """'Fri 27 Nov 23:59': the format used in every tool result and answer."""
    local = to_local(moment)
    return f"{fmt_day(local.date())} {local:%H:%M}"


def iso(moment: datetime) -> str:
    return to_local(moment).isoformat(timespec="minutes")


def minutes_between(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() // 60)


def ceil_to_quarter(moment: datetime) -> datetime:
    local = to_local(moment).replace(second=0, microsecond=0)
    extra = (-local.minute) % 15
    return local + timedelta(minutes=extra)
