"""A fictional semester (made-up course codes) used by `study demo`, the eval set and the tests.

The clock is frozen at Mon 23 Nov 2026 08:00 Helsinki time so every replay is reproducible.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

from study_agent.config import Settings
from study_agent.models import short_id
from study_agent.store import Store
from study_agent.timeutil import TZ, fixed_clock

DEMO_DIR = Path(__file__).parent
DEADLINES = DEMO_DIR / "mycourses-demo.ics"
TIMETABLE = DEMO_DIR / "timetable-demo.ics"
DEMO_NOW = datetime(2026, 11, 23, 8, 0, tzinfo=TZ)
demo_clock = fixed_clock(DEMO_NOW)

# Deadline ids are stable hashes of the calendar UIDs.
A2 = short_id("D", "1001@mycourses.example")
A3 = short_id("D", "1004@mycourses.example")
LAB4 = short_id("D", "1006@mycourses.example")
MILESTONE2 = short_id("D", "1008@mycourses.example")


def demo_settings(data_dir: Path, base: Settings | None = None) -> Settings:
    settings = base or Settings(data_dir=data_dir)
    return replace(
        settings,
        data_dir=data_dir,
        deadlines_source=str(DEADLINES),
        timetable_source=str(TIMETABLE),
    )


def seed(store: Store) -> None:
    """The state a real user would have after a few days of use."""
    store.set_deadline_done(A2, datetime(2026, 11, 18, 21, 30, tzinfo=TZ))
    store.set_deadline_estimate(MILESTONE2, 1500)
    store.log_progress(
        "Read the Assignment 3 handout and set up the repo",
        datetime(2026, 11, 22, 18, 0, tzinfo=TZ),
        item_id=A3,
        course="CS-E9001",
        minutes=60,
    )
    store.add_task(
        "Review lecture notes, weeks 10-11",
        datetime(2026, 11, 22, 18, 5, tzinfo=TZ),
        course="CS-E9001",
        estimate_min=90,
    )
    store.add_block(
        LAB4,
        "ELEC-C9003",
        "Lab report 4",
        datetime(2026, 11, 26, 18, 0, tzinfo=TZ),
        datetime(2026, 11, 26, 19, 30, tzinfo=TZ),
        datetime(2026, 11, 22, 18, 10, tzinfo=TZ),
    )
    store.add_block(
        MILESTONE2,
        "CS-E9001",
        "Project milestone 2",
        datetime(2026, 11, 28, 10, 0, tzinfo=TZ),
        datetime(2026, 11, 28, 12, 0, tzinfo=TZ),
        datetime(2026, 11, 22, 18, 10, tzinfo=TZ),
    )
