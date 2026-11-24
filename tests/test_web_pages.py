"""The product API behind the web pages: reads, button actions (preview, then apply) and admin."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from study_agent.config import Settings
from study_agent.web import create_app

HEADERS = {"X-Study-Agent": "1"}


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings, demo=True, allowed_hosts=("testserver",)))


def test_overview_stats(client: TestClient) -> None:
    data = client.get("/api/overview").json()
    assert data["stats"] == {
        "due_this_week": 4,
        "at_risk": 2,
        "tight": 2,
        "planned_today_min": 300,
        "logged_week_min": 60,
        "done_week": 1,
    }
    assert data["coming_up"][0]["title"] == "Assignment 3: Tool-calling agent"


def test_items_and_detail(client: TestClient) -> None:
    data = client.get("/api/items", params={"include_done": True}).json()
    assert data["now"].startswith("2026-11-23T08:00")
    assert {i["kind"] for i in data["items"]} == {"deadline", "task"}
    assert [d["title"] for d in data["done"]] == ["Assignment 2: Prompt evaluation"]
    assert data["courses"] == ["CS-E9001", "ELEC-C9003", "MS-C9002", "TU-E9004"]

    detail = client.get("/api/items/D-cbe576").json()
    assert detail["item"]["logged_min"] == 60
    assert detail["progress"][0]["minutes"] == 60
    assert client.get("/api/items/D-nope").status_code == 404


def test_week_view(client: TestClient) -> None:
    week = client.get("/api/week").json()
    assert week["label"] == "Mon 23 Nov - Sun 29 Nov"
    assert len(week["days"]) == 7 and len(week["events"]) == 7
    assert {b["block_id"] for b in week["saved"]} == {"B-1", "B-2"}
    assert week["suggested"] and all(s["start"] >= "2026-11-23T08:00" for s in week["suggested"])
    assert client.get("/api/week", params={"start": "2026-12-02"}).json()["start"] == "2026-11-30"
    assert client.get("/api/week", params={"start": "soon"}).status_code == 400


def test_progress_stats_settings(client: TestClient) -> None:
    progress = client.get("/api/progress", params={"days": 7}).json()
    assert len(progress["per_day"]) == 7
    assert progress["per_course"] == {"CS-E9001": 60}
    assert client.get("/api/stats").json() == {"report": None, "recent": []}
    settings = client.get("/api/settings").json()
    assert settings["deadlines_source"] == "mycourses-demo.ics"
    assert len(settings["tools"]) == 14


def test_button_actions_preview_then_apply(client: TestClient) -> None:
    preview = client.post(
        "/api/actions/complete_item/preview", json={"item_id": "D-cbe576"}, headers=HEADERS
    ).json()
    assert preview == {
        "action": "Mark as done",
        "lines": ["D-cbe576 CS-E9001: Assignment 3: Tool-calling agent"],
    }
    # A preview never writes.
    assert any(i["id"] == "D-cbe576" for i in client.get("/api/items").json()["items"])

    applied = client.post(
        "/api/actions/complete_item", json={"item_id": "D-cbe576"}, headers=HEADERS
    ).json()
    assert applied["status"] == "applied"
    assert not any(i["id"] == "D-cbe576" for i in client.get("/api/items").json()["items"])


def test_button_actions_use_tool_validation(client: TestClient) -> None:
    late = client.post(
        "/api/actions/move_study_block",
        json={"block_id": "B-1", "new_start": "2026-11-28T10:00"},
        headers=HEADERS,
    )
    assert late.status_code == 400
    assert "Fri 27 Nov 23:59" in late.json()["detail"]
    assert client.post("/api/actions/get_deadlines", json={}, headers=HEADERS).status_code == 404
    assert (
        client.post("/api/actions/complete_item", json={"item_id": "D-cbe576"}).status_code == 403
    )

    moved = client.post(
        "/api/actions/move_study_block",
        json={"block_id": "B-2", "new_start": "2026-11-29T13:00"},
        headers=HEADERS,
    )
    assert moved.json()["now_at"] == "Sun 29 Nov 13:00-15:00"
    removed = client.post(
        "/api/actions/delete_study_block", json={"block_id": "B-2"}, headers=HEADERS
    )
    assert removed.json()["deleted"] == "B-2"
    assert {b["block_id"] for b in client.get("/api/week").json()["saved"]} == {"B-1"}


def test_eval_and_sync_endpoints(client: TestClient) -> None:
    result = client.post("/api/eval", headers=HEADERS).json()
    assert result["summary"]["passed"] == result["summary"]["cases"] >= 20
    assert client.post("/api/sync", headers=HEADERS).status_code == 400  # demo calendars
