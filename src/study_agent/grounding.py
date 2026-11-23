"""Grounding checker: every date, time and course code in an answer must come from a tool result.

After the model answers, this module extracts the checkable facts from the answer text and
compares them with the facts in this run's tool results (plus the question and the current time,
which my code supplied). Anything that is not there is reported as an ungrounded claim. It also
catches a classic language-model error: a weekday that does not match the date ("Fri 28 Nov"
when 28 Nov 2026 is a Saturday).

This is a deterministic check, not a second model call, so it costs nothing and cannot itself
hallucinate. It cannot judge free-text reasoning; it checks the facts that matter for deadlines.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from study_agent.models import COURSE_CODE_RE

MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
        start=1,
    )
}
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_WD = r"(?:(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+)?"
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_YEAR = r"(?:,?\s+(20\d{2}))?"

ISO_DATE_RE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
DAY_MONTH_RE = re.compile(rf"\b{_WD}(\d{{1,2}})(?:st|nd|rd|th)?\.?\s+{_MON}{_YEAR}", re.I)
MONTH_DAY_RE = re.compile(rf"\b{_WD}{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?(?![\d:]){_YEAR}", re.I)
FINNISH_DATE_RE = re.compile(rf"\b{_WD}(\d{{1,2}})\.(\d{{1,2}})\.(20\d{{2}})?(?!\d)", re.I)
TIME_RE = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")


@dataclass(frozen=True)
class Claim:
    kind: str  # date | time | course | weekday
    text: str
    value: str


@dataclass
class GroundingReport:
    checked: int = 0
    unsupported: list[Claim] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unsupported

    def describe(self) -> str:
        return "; ".join(f"{c.kind} '{c.text}'" for c in self.unsupported)


def _resolve_year(month: int, day: int, year: int | None, today: date) -> date | None:
    try:
        if year:
            return date(year, month, day)
        options = [date(today.year + offset, month, day) for offset in (-1, 0, 1)]
    except ValueError:
        return None
    return min(options, key=lambda d: abs((d - today).days))


def _dates(text: str, today: date) -> list[tuple[str, date, str | None]]:
    """(matched text, date, weekday-as-written or None) for every date in ``text``."""
    found: list[tuple[str, date, str | None]] = []
    for m in ISO_DATE_RE.finditer(text):
        try:
            found.append((m.group(0), date(int(m[1]), int(m[2]), int(m[3])), None))
        except ValueError:
            continue
    taken: list[tuple[int, int]] = []
    for m in DAY_MONTH_RE.finditer(text):
        taken.append(m.span())
        d = _resolve_year(MONTHS[m[3].lower()[:3]], int(m[2]), int(m[4]) if m[4] else None, today)
        if d:
            found.append((m.group(0).strip(), d, m[1]))
    for m in MONTH_DAY_RE.finditer(text):
        if any(m.start() < end and start < m.end() for start, end in taken):
            continue  # "27 Nov 2026" was already read as day-month
        d = _resolve_year(MONTHS[m[2].lower()[:3]], int(m[3]), int(m[4]) if m[4] else None, today)
        if d:
            found.append((m.group(0).strip(), d, m[1]))
    for m in FINNISH_DATE_RE.finditer(text):
        month, day = int(m[3]), int(m[2])
        if 1 <= month <= 12:
            d = _resolve_year(month, day, int(m[4]) if m[4] else None, today)
            if d:
                found.append((m.group(0).strip(), d, m[1]))
    return found


def _times(text: str) -> list[tuple[str, str]]:
    return [(m.group(0), f"{int(m[1]):02d}:{m[2]}") for m in TIME_RE.finditer(text)]


@dataclass
class FactIndex:
    """Facts that my code put in front of the model during one run."""

    today: date
    dates: set[date] = field(default_factory=set)
    times: set[str] = field(default_factory=set)
    courses: set[str] = field(default_factory=set)

    def add(self, text: str) -> None:
        self.dates.update(d for _, d, _ in _dates(text, self.today))
        self.times.update(t for _, t in _times(text))
        self.courses.update(COURSE_CODE_RE.findall(text))

    def add_all(self, texts: Iterable[str]) -> None:
        for text in texts:
            self.add(text)


def check(answer: str, facts: FactIndex) -> GroundingReport:
    report = GroundingReport()
    seen: set[tuple[str, str]] = set()

    def flag(claim: Claim) -> None:
        if (claim.kind, claim.value) not in seen:
            seen.add((claim.kind, claim.value))
            report.unsupported.append(claim)

    for text, day, weekday in _dates(answer, facts.today):
        report.checked += 1
        if weekday and WEEKDAYS[day.weekday()] != weekday.lower()[:3]:
            flag(Claim("weekday", text, f"{day.isoformat()} is a {day:%A}"))
        elif day not in facts.dates and day != facts.today:
            flag(Claim("date", text, day.isoformat()))
    for text, value in _times(answer):
        report.checked += 1
        if value not in facts.times:
            flag(Claim("time", text, value))
    for code in COURSE_CODE_RE.findall(answer):
        report.checked += 1
        if code not in facts.courses:
            flag(Claim("course", code, code))
    return report
