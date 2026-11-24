"""Courses I type in: storage, deadlines/events, the add_deadline tool and the web API."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from study_agent import courses
from study_agent.config import Settings
from study_agent.llm import LLMError
from study_agent.sources.loader import Calendars
from study_agent.store import Store
from study_agent.timeutil import TZ, fixed_clock
from study_agent.tools import ToolContext, execute
from study_agent.web import create_app

NOW = datetime(2026, 11, 24, 9, 0, tzinfo=TZ)
HEADERS = {"X-Study-Agent": "1"}


@pytest.fixture
def mine(tmp_path: Path) -> ToolContext:
    """A context with no imported calendars at all: only what I type in."""
    settings = Settings(data_dir=tmp_path)
    store = Store(settings.db_path)
    clock = fixed_clock(NOW)
    return ToolContext(settings, store, Calendars(settings, clock), clock)


def test_typed_in_deadlines_become_items_with_estimates_and_risk(mine: ToolContext) -> None:
    store = mine.store
    store.add_course("CS-E4780", "Scalable Systems", NOW)
    exam = store.add_course_deadline(
        "CS-E4780", "exam", "Final exam", datetime(2026, 12, 14, 9, 0, tzinfo=TZ), NOW
    )
    store.add_course_deadline(
        "CS-E4780", "project", "Report", datetime(2026, 12, 4, 23, 59, tzinfo=TZ), NOW
    )
    store.set_deadline_estimate(f"M-{exam}", 1200)

    items = {i.title: i for i in mine.items()}
    assert items["Final exam"].id == f"M-{exam}"
    assert items["Final exam"].category == "exam"
    assert items["Final exam"].estimate_min == 1200  # mine
    assert items["Report"].estimate_min == 900  # project default
    assert items["Report"].source == "my courses"
    risks = mine.risks(mine.items())
    assert {r.status for r in risks.values()} <= {"ok", "tight", "at_risk"}

    result = json.loads(
        execute("get_deadlines", {"to_date": "2026-12-31"}, mine, lambda p: False).content
    )
    exam_row = next(d for d in result["deadlines"] if d["title"] == "Final exam")
    assert exam_row["due_local"] == "Mon 14 Dec 09:00"
    assert exam_row["category"] == "exam"


def test_weekly_classes_expand_across_dst_and_block_study_time(mine: ToolContext) -> None:
    store = mine.store
    store.add_course("MS-A0503", "Probability", NOW)
    store.add_session(
        "MS-A0503", "lecture", 0, time(10, 15), time(12, 0), date(2026, 10, 19), date(2026, 12, 11)
    )
    events = courses.events(store, date(2026, 10, 19), date(2026, 11, 1))
    assert [e.start.date() for e in events] == [date(2026, 10, 19), date(2026, 10, 26)]
    assert all((e.start.hour, e.start.minute) == (10, 15) for e in events)
    assert events[0].start.utcoffset() == timedelta(hours=3)
    assert events[1].start.utcoffset() == timedelta(hours=2)
    assert events[0].title == "MS-A0503 Probability lecture"

    # The planner keeps suggestions out of class time.
    store.add_course_deadline(
        "MS-A0503", "assignment", "Round 6", datetime(2026, 12, 1, 12, 0, tzinfo=TZ), NOW
    )
    plan = json.loads(execute("plan_day", {"date": "2026-11-30"}, mine, lambda p: False).content)
    assert [e["when"] for e in plan["events"]] == ["Mon 30 Nov 10:15-12:00"]
    for block in plan["suggested_blocks"]:
        assert block["end"][:16] <= "2026-11-30T10:15" or block["start"][:16] >= "2026-11-30T12:00"


def test_add_deadline_tool_needs_approval_and_validates(mine: ToolContext) -> None:
    args = {"course": "cs-e4780", "kind": "exam", "title": "Final exam", "due": "2026-12-14T09:00"}
    declined = execute("add_deadline", args, mine, lambda p: False)
    assert json.loads(declined.content)["status"] == "declined"
    assert mine.store.course_deadlines() == [] and mine.store.courses() == []

    applied = json.loads(
        execute("add_deadline", {**args, "estimate_min": 600}, mine, lambda p: True).content
    )
    assert applied["deadline_id"] == "M-1"
    assert "Due: Mon 14 Dec 09:00" in applied["change"]
    assert [c.code for c in mine.store.courses()] == ["CS-E4780"]  # created on the fly
    assert mine.store.deadline_states()["M-1"].estimate_min == 600

    for bad in ({**args, "kind": "party"}, {**args, "due": "next week"}, {**args, "course": "!!"}):
        assert execute("add_deadline", bad, mine, lambda p: True).is_error


def test_deleting_a_course_cleans_up_its_state(mine: ToolContext) -> None:
    store = mine.store
    store.add_course("TU-C1030", "Industrial Engineering", NOW)
    due = datetime(2026, 12, 7, 23, 59, tzinfo=TZ)
    ref = f"M-{store.add_course_deadline('TU-C1030', 'assignment', 'Case', due, NOW)}"
    store.set_deadline_estimate(ref, 300)
    store.add_block(
        ref,
        "TU-C1030",
        "Case",
        datetime(2026, 11, 30, 9, 0, tzinfo=TZ),
        datetime(2026, 11, 30, 10, 0, tzinfo=TZ),
        NOW,
    )
    store.add_session(
        "TU-C1030", "lecture", 4, time(12, 15), time(14, 0), date(2026, 11, 1), date(2026, 12, 11)
    )
    store.delete_course("TU-C1030")
    assert store.courses() == [] and store.course_deadlines() == [] and store.sessions() == []
    assert ref not in store.deadline_states()
    assert store.blocks_for(ref) == []


@pytest.mark.parametrize(
    ("raw", "ok"),
    [("cs-e4780", "CS-E4780"), (" MS A0503 ", "MS-A0503"), ("ELEC-C7310", "ELEC-C7310")],
)
def test_course_codes_are_normalised(raw: str, ok: str) -> None:
    assert courses.clean_code(raw) == ok


def test_due_parsing() -> None:
    assert courses.parse_due("2026-12-14") == datetime(2026, 12, 14, 23, 59, tzinfo=TZ)
    assert courses.parse_due("2026-12-14T09:00") == datetime(2026, 12, 14, 9, 0, tzinfo=TZ)
    with pytest.raises(courses.CourseInputError):
        courses.parse_due("14/12")


# --- web API ----------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(Settings(data_dir=tmp_path / "web"), allowed_hosts=("testserver",))
    )


def test_course_api_end_to_end(client: TestClient) -> None:
    overview = client.get("/api/overview").json()
    assert overview["setup"] == {"courses": 0, "calendar_connected": False, "has_data": False}
    assert overview["warnings"] == []  # no nagging about a calendar URL I don't need

    assert (
        client.post(
            "/api/courses", json={"code": "cs-e4780", "name": "Scalable Systems"}
        ).status_code
        == 403
    )
    assert client.post(
        "/api/courses", json={"code": "cs-e4780", "name": "Scalable Systems"}, headers=HEADERS
    ).json() == {"code": "CS-E4780"}
    assert (
        client.post("/api/courses", json={"code": "CS-E4780"}, headers=HEADERS).status_code == 409
    )
    assert client.post("/api/courses", json={"code": "!"}, headers=HEADERS).status_code == 400

    exam = client.post(
        "/api/courses/CS-E4780/deadlines",
        json={"kind": "exam", "title": "Final exam", "due": "2026-12-14T09:00"},
        headers=HEADERS,
    ).json()
    assert exam == {"id": "M-1"}
    assert (
        client.post(
            "/api/courses/NOPE-1/deadlines",
            json={"title": "x", "due": "2026-12-01"},
            headers=HEADERS,
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/courses/CS-E4780/deadlines",
            json={"kind": "party", "title": "x", "due": "2026-12-01"},
            headers=HEADERS,
        ).status_code
        == 400
    )

    session = {
        "kind": "lecture",
        "weekday": 0,
        "start": "10:15",
        "end": "12:00",
        "first_day": "2026-11-02",
        "last_day": "2026-12-11",
    }
    assert client.post("/api/courses/CS-E4780/sessions", json=session, headers=HEADERS).json() == {
        "id": 1
    }
    backwards = {**session, "start": "12:00", "end": "10:15"}
    assert (
        client.post("/api/courses/CS-E4780/sessions", json=backwards, headers=HEADERS).status_code
        == 400
    )

    listing = client.get("/api/courses").json()
    [course] = listing["courses"]
    assert course["deadlines"][0]["due_local"] == "Mon 14 Dec 09:00"
    assert course["sessions"][0]["start"] == "10:15"
    assert "exam" in listing["kinds"]

    assert client.patch(
        "/api/deadlines/M-1",
        json={
            "kind": "exam",
            "title": "Final exam (hall A)",
            "due": "2026-12-15T09:00",
            "estimate_min": 900,
        },
        headers=HEADERS,
    ).json() == {"ok": True}
    item = client.get("/api/items/M-1").json()["item"]
    assert (item["title"], item["due_local"], item["estimate_min"]) == (
        "Final exam (hall A)",
        "Tue 15 Dec 09:00",
        900,
    )

    calendar = client.get("/api/calendar", params={"month": "2026-12"}).json()
    assert calendar["label"] == "December 2026"
    assert (calendar["grid_start"], calendar["grid_end"]) == ("2026-11-30", "2027-01-03")
    assert [d["title"] for d in calendar["deadlines"]] == ["Final exam (hall A)"]
    assert (
        len([e for e in calendar["events"] if e["course"] == "CS-E4780"]) == 2
    )  # Mondays 30 Nov, 7 Dec
    assert client.get("/api/calendar", params={"month": "soon"}).status_code == 400

    assert client.get("/api/overview").json()["setup"]["has_data"] is True
    assert client.delete("/api/deadlines/M-1", headers=HEADERS).json() == {"ok": True}
    assert client.delete("/api/deadlines/M-1", headers=HEADERS).status_code == 404
    assert client.delete("/api/courses/CS-E4780", headers=HEADERS).json() == {"ok": True}
    assert client.get("/api/courses").json()["courses"] == []


def test_old_databases_get_the_weight_column(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE course_deadlines (id INTEGER PRIMARY KEY, course TEXT NOT NULL,"
        " kind TEXT NOT NULL, title TEXT NOT NULL, due TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()
    store = Store(path)
    columns = {r["name"] for r in store.conn.execute("PRAGMA table_info(course_deadlines)")}
    assert "weight_pct" in columns
    store.close()


def test_grade_weight_reaches_the_agent(mine: ToolContext) -> None:
    args = {
        "course": "CS-E4780",
        "kind": "project",
        "title": "Course project",
        "due": "2026-12-04",
        "weight_pct": 40,
    }
    applied = json.loads(execute("add_deadline", args, mine, lambda p: True).content)
    assert "Grade weight: 40 %" in applied["change"]
    result = json.loads(
        execute("get_deadlines", {"to_date": "2026-12-31"}, mine, lambda p: False).content
    )
    assert result["deadlines"][0]["grade_weight_pct"] == 40
    assert execute("add_deadline", {**args, "weight_pct": 140}, mine, lambda p: True).is_error


def test_api_key_is_checked_saved_and_never_echoed(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("STUDY_EFFORT=low\nANTHROPIC_API_KEY=\n", encoding="utf-8")

    def checker(key: str) -> None:  # stands in for the real call to Anthropic
        if key.endswith("bad"):
            raise LLMError("Anthropic rejected this key")

    app = create_app(
        Settings(data_dir=tmp_path / "d"),
        allowed_hosts=("testserver",),
        env_file=env,
        key_checker=checker,
    )
    client = TestClient(app)
    url = "/api/settings/api-key"
    good = "sk-ant-api03-" + "x" * 40 + "good"

    malformed = client.post(url, json={"api_key": "hello-world-123"}, headers=HEADERS)
    assert malformed.status_code == 400
    rejected = client.post(url, json={"api_key": good[:-4] + "xbad"}, headers=HEADERS)
    assert rejected.status_code == 400 and "rejected" in rejected.json()["detail"]
    assert "ANTHROPIC_API_KEY=\n" in env.read_text(encoding="utf-8")  # nothing saved

    saved = client.post(url, json={"api_key": good}, headers=HEADERS).json()
    assert saved == {"api_key": "sk-ant-api03…good"}
    text = env.read_text(encoding="utf-8")
    assert f"ANTHROPIC_API_KEY={good}" in text and "STUDY_EFFORT=low" in text
    shown = client.get("/api/settings").json()
    assert shown["api_key"] == "sk-ant-api03…good" and good not in json.dumps(shown)
    assert client.post(url, json={"api_key": good}).status_code == 403  # CSRF guard


def test_renaming_a_deadline_renames_its_saved_blocks(mine: ToolContext) -> None:
    store = mine.store
    store.add_course("CS-E4850", "Computer Vision", NOW)
    due = datetime(2026, 11, 27, 23, 59, tzinfo=TZ)
    number = store.add_course_deadline("CS-E4850", "assignment", "Assignment 12", due, NOW)
    start = datetime(2026, 11, 25, 10, 0, tzinfo=TZ)
    store.add_block(
        f"M-{number}", "CS-E4850", "Assignment 12", start, start + timedelta(hours=1), NOW
    )
    noon = datetime(2026, 11, 27, 11, 59, tzinfo=TZ)
    store.update_course_deadline(number, "assignment", "Exercise round 12", noon)
    assert [b.title for b in store.blocks_for(f"M-{number}")] == ["Exercise round 12"]
