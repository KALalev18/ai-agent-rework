"""Domain objects. Only these fields reach the model: course code, title, times, source label."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

from study_agent.timeutil import fmt_moment, iso

# Aalto-style course codes: CS-E4780, MS-A0111, ELEC-C7310, TU-E9004 ...
COURSE_CODE_RE = re.compile(r"\b[A-Z]{2,5}-[A-Z]?\d{3,5}[A-Z]?\b")


def find_course_code(*texts: str) -> str | None:
    for text in texts:
        match = COURSE_CODE_RE.search(text or "")
        if match:
            return match.group(0)
    return None


CATEGORY_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("exam", ("exam", "tentti", "midterm")),
    ("project", ("project", "milestone")),
    ("quiz", ("quiz",)),
    ("lab", ("lab",)),
    ("presentation", ("presentation",)),
    ("assignment", ("assignment", "exercise", "round", "homework")),
)


def infer_category(title: str) -> str:
    """Category of an imported deadline from words in its title; 'deadline' if none match."""
    lowered = title.lower()
    for category, words in CATEGORY_WORDS:
        if any(word in lowered for word in words):
            return category
    return "deadline"


def short_id(prefix: str, key: str) -> str:
    """Stable short id (e.g. 'D-3fa2c1') so the model and I can refer to items easily."""
    return f"{prefix}-{hashlib.sha1(key.encode()).hexdigest()[:6]}"


@dataclass(frozen=True)
class Deadline:
    id: str
    course: str
    title: str
    due: datetime
    source: str
    category: str = "deadline"  # assignment, project, exam, quiz, lab, presentation, ...
    course_name: str = ""
    weight_pct: int | None = None  # share of the course grade, when I know it

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "id": self.id,
            "course": self.course,
            "title": self.title,
            "category": self.category,
            "due": iso(self.due),
            "due_local": fmt_moment(self.due),
            "source": self.source,
        }
        if self.weight_pct is not None:
            data["grade_weight_pct"] = self.weight_pct
        return data


@dataclass(frozen=True)
class Event:
    course: str
    kind: str
    title: str
    start: datetime
    end: datetime
    source: str

    def to_dict(self) -> dict[str, object]:
        return {
            "course": self.course,
            "kind": self.kind,
            "title": self.title,
            "start": iso(self.start),
            "end": iso(self.end),
            "when": f"{fmt_moment(self.start)}-{self.end:%H:%M}",
            "source": self.source,
        }
