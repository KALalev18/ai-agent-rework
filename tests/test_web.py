"""Web UI backend: dashboard data, security checks and the approval handshake over HTTP."""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from study_agent.config import Settings
from study_agent.llm import ReplayModel, script_to_responses
from study_agent.store import Store
from study_agent.web import create_app

HEADERS = {"X-Study-Agent": "1"}
HOSTS = ("testserver",)


def wait_for(client: TestClient, run_id: str, statuses: set[str]) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        data = client.get(f"/api/runs/{run_id}", params={"after": len(events)}).json()
        events += data["events"]
        if data["status"] in statuses:
            return {"status": data["status"], "events": events}
        time.sleep(0.02)
    raise AssertionError(f"run never reached {statuses}; events: {events}")


def live_client(settings: Settings, script: list[dict[str, Any]]) -> TestClient:
    app = create_app(
        settings,
        client_factory=lambda: ReplayModel(script_to_responses(script)),
        allowed_hosts=HOSTS,
    )
    return TestClient(app)


def test_index_and_demo_overview(settings: Settings) -> None:
    client = TestClient(create_app(settings, demo=True, allowed_hosts=HOSTS))
    assert "Study Agent" in client.get("/").text
    assert client.get("/static/js/main.js").status_code == 200
    data = client.get("/api/overview").json()
    assert data["mode"] == "demo"
    assert data["now"] == "Mon 23 Nov 08:00"
    assert {d["title"] for d in data["coming_up"]} >= {"Lab report 4", "Quiz 5"}
    assert any(r["risk"] == "at_risk" for r in data["today"]["at_risk"])
    assert data["today"]["events"] and data["today"]["suggested_blocks"]
    assert len(data["demo_questions"]) >= 20


def test_posts_need_the_app_header_and_hosts_are_checked(settings: Settings) -> None:
    client = TestClient(create_app(settings, demo=True, allowed_hosts=HOSTS))
    assert client.post("/api/ask", json={"question": "hi"}).status_code == 403
    assert client.post("/api/runs/x/decision", json={"approve": True}).status_code == 403
    evil = TestClient(create_app(settings, demo=True), base_url="http://evil.example")
    assert evil.get("/api/overview").status_code == 400


def test_demo_question_runs_with_scripted_approval(settings: Settings) -> None:
    client = TestClient(create_app(settings, demo=True, allowed_hosts=HOSTS))
    question = (
        "I finished or worked on this: finished exercise round 8, took about 3 hours. Log it."
    )
    run_id = client.post("/api/ask", json={"question": question}, headers=HEADERS).json()["run_id"]
    result = wait_for(client, run_id, {"done", "error"})
    kinds = [e["kind"] for e in result["events"]]
    assert result["status"] == "done"
    assert "tool_call" in kinds and "proposal" in kinds and kinds[-1] == "answer"
    proposal = next(e for e in result["events"] if e["kind"] == "proposal")
    assert proposal["scripted"] is True
    assert result["events"][-1]["grounded"] is True


def test_unknown_demo_question_is_an_error(settings: Settings) -> None:
    client = TestClient(create_app(settings, demo=True, allowed_hosts=HOSTS))
    run_id = client.post("/api/ask", json={"question": "free text"}, headers=HEADERS).json()[
        "run_id"
    ]
    assert wait_for(client, run_id, {"done", "error"})["status"] == "error"


@pytest.mark.parametrize("approve", [True, False])
def test_live_approval_handshake(settings: Settings, store: Store, approve: bool) -> None:
    script = [
        {"tools": [{"name": "complete_item", "input": {"item_id": "T-1"}}]},
        {"text": "Done." if approve else "Left it as it was."},
    ]
    client = live_client(settings, script)
    run_id = client.post("/api/ask", json={"question": "Mark T-1 done"}, headers=HEADERS).json()[
        "run_id"
    ]
    waiting = wait_for(client, run_id, {"waiting_approval"})
    proposal = next(e for e in waiting["events"] if e["kind"] == "proposal")
    assert proposal["action"] == "Mark as done"
    assert store.task(1).done_at is None  # nothing is written while waiting

    response = client.post(
        f"/api/runs/{run_id}/decision", json={"approve": approve}, headers=HEADERS
    )
    assert response.status_code == 200
    final = wait_for(client, run_id, {"done", "error"})
    assert final["status"] == "done"
    assert (store.task(1).done_at is not None) is approve
    assert (
        client.post(
            f"/api/runs/{run_id}/decision", json={"approve": True}, headers=HEADERS
        ).status_code
        == 409
    )


def test_live_run_is_logged_and_errors_are_reported(settings: Settings, store: Store) -> None:
    client = live_client(settings, [{"text": "Hello."}])
    run_id = client.post("/api/ask", json={"question": "hi"}, headers=HEADERS).json()["run_id"]
    assert wait_for(client, run_id, {"done", "error"})["status"] == "done"
    assert [r["command"] for r in store.runs()] == ["web"]

    empty = live_client(settings, [])  # the model has nothing to say: replay is exhausted
    run_id = empty.post("/api/ask", json={"question": "hi"}, headers=HEADERS).json()["run_id"]
    result = wait_for(empty, run_id, {"done", "error"})
    assert result["status"] == "error"
    assert "no more model responses" in result["events"][-1]["message"]


def test_unknown_run_is_404(settings: Settings) -> None:
    client = TestClient(create_app(settings, demo=True, allowed_hosts=HOSTS))
    assert client.get("/api/runs/nope").status_code == 404
