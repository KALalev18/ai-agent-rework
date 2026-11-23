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

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "course": self.course,
            "title": self.title,
            "due": iso(self.due),
            "due_local": fmt_moment(self.due),
            "source": self.source,
        }


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
