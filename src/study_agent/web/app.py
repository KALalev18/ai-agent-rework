"""FastAPI backend for the local web UI.

Design:
- The dashboard (risks, today's timeline, deadlines) calls the deterministic tools directly. No
  model call, so it is free and instant.
- A question starts the same Agent as the CLI in a background thread. The browser polls
  /api/runs/{id} for new events (tool calls, proposals, the answer). Polling is simpler than
  WebSockets or SSE and is plenty for one user on localhost.
- Human approval: when a write tool returns a proposal, the agent thread blocks until I click
  Approve or Decline (POST /api/runs/{id}/decision). No answer in 10 minutes means declined.

Security for a local app: it binds to 127.0.0.1, only accepts localhost Host headers (blocks DNS
rebinding), and every POST needs an `X-Study-Agent` header. Browsers cannot add custom headers
to cross-site requests without a CORS preflight, which this app never allows, so another website
cannot start runs or approve changes through my browser.
"""

from __future__ import annotations

import logging
import tempfile
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from study_agent.agent import Agent, AgentResult
from study_agent.config import Settings
from study_agent.demo import demo_clock, demo_settings, seed
from study_agent.evals import load_cases, run_case
from study_agent.llm import AnthropicModel, LLMError, ModelClient
from study_agent.render import footer
from study_agent.runlog import log_run
from study_agent.sources.loader import Calendars, SourceError
from study_agent.store import Store
from study_agent.timeutil import Clock, fmt_moment, system_clock
from study_agent.tools import Proposal, ToolContext, get_deadlines, plan_day

STATIC_DIR = Path(__file__).parent / "static"
APPROVAL_TIMEOUT_S = 600
MAX_RUNS_KEPT = 50
log = logging.getLogger(__name__)


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=64)


class DecisionBody(BaseModel):
    approve: bool


@dataclass
class Run:
    """One agent run: an append-only event list the browser polls, plus the approval handshake."""

    id: str
    status: str = "running"  # running | waiting_approval | done | error
    events: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _decided: threading.Event = field(default_factory=threading.Event)
    _decision: bool = False

    def emit(self, kind: str, data: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.events.append({"kind": kind, **(data or {})})

    def since(self, index: int) -> list[dict[str, Any]]:
        with self._lock:
            return self.events[index:]

    def trace(self, kind: str, data: dict[str, Any]) -> None:
        """Agent event hook. Successful tool results are summarised, not sent in full."""
        if kind == "tool_result":
            payload = {k: data[k] for k in ("name", "ok", "approved")}
            if not data["ok"]:
                payload["error"] = data["content"]
            self.emit(kind, payload)
        else:
            self.emit(kind, data)

    def ask_approval(self, proposal: Proposal) -> bool:
        """Called from the agent thread. Blocks until the browser sends a decision."""
        self._decided.clear()
        self.status = "waiting_approval"  # before the event, so an instant click is accepted
        self.emit("proposal", {"action": proposal.action, "lines": proposal.lines})
        answered = self._decided.wait(APPROVAL_TIMEOUT_S)
        self.status = "running"
        if not answered:
            self.emit("info", {"text": "No answer within 10 minutes, so the change was declined."})
            return False
        return self._decision

    def decide(self, approve: bool) -> None:
        self._decision = approve
        self.emit("decision", {"approved": approve})
        self._decided.set()


def _answer(result: AgentResult, mode: str, settings: Settings) -> dict[str, Any]:
    return {
        "text": result.answer,
        "footer": footer(result, mode, settings.eur_per_usd),
        "grounded": result.grounding.ok,
        "unsupported": result.grounding.describe(),
        "corrected": result.corrected,
    }


def create_app(
    settings: Settings,
    demo: bool = False,
    client_factory: Callable[[], ModelClient] = AnthropicModel,
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost"),
) -> FastAPI:
    clock: Clock = system_clock
    demo_cases = {c.question: c for c in load_cases()} if demo else {}
    if demo:
        # A seeded copy of the fictional semester, so the dashboard has something to show.
        demo_dir = Path(tempfile.mkdtemp(prefix="study-agent-demo-"))
        settings = demo_settings(demo_dir, replace(settings, data_dir=demo_dir))
        seed_store = Store(settings.db_path)
        seed(seed_store)
        seed_store.close()
        clock = demo_clock

    app = FastAPI(title="Study Agent", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    runs: dict[str, Run] = {}
    conversations: dict[str, list[dict[str, Any]]] = {}

    def require_app_header(x_study_agent: str | None = Header(default=None)) -> None:
        if x_study_agent != "1":
            raise HTTPException(403, "missing X-Study-Agent header")

    def open_context() -> tuple[Store, ToolContext]:
        # One SQLite connection per request/thread: sqlite3 objects must stay in their thread.
        store = Store(settings.db_path)
        return store, ToolContext(settings, store, Calendars(settings, clock), clock)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/overview")
    def overview() -> dict[str, Any]:
        store, ctx = open_context()
        try:
            horizon = ctx.today() + timedelta(days=14)
            deadlines = get_deadlines(ctx, {"to_date": horizon.isoformat()})
            today = plan_day(ctx, {})
        except SourceError as exc:
            raise HTTPException(503, str(exc)) from None
        finally:
            store.close()
        return {
            "mode": "demo" if demo else "live",
            "model": settings.model,
            "now": fmt_moment(ctx.now()),
            "today": today,
            "deadlines": deadlines["deadlines"],
            "warnings": ctx.calendars.warnings,
            "demo_questions": list(demo_cases),
        }

    def run_live(run: Run, body: AskBody) -> None:
        store, ctx = open_context()
        try:
            agent = Agent(ctx, client_factory(), settings, run.ask_approval, run.trace)
            conversation = body.conversation_id or run.id
            result = agent.run(body.question, conversations.get(conversation, []))
            conversations[conversation] = result.messages
            log_run(store, "web", body.question, result, settings)
            run.emit("answer", _answer(result, "live", settings))
            run.status = "done"
        except (LLMError, SourceError) as exc:
            run.emit("error", {"message": str(exc)})
            run.status = "error"
        except Exception:
            log.exception("agent run failed")
            run.emit("error", {"message": "unexpected error; see the server log"})
            run.status = "error"
        finally:
            store.close()

    def run_demo(run: Run, body: AskBody) -> None:
        case = demo_cases.get(body.question)
        if case is None:
            run.emit("error", {"message": "Demo mode replays scripted answers: pick a question."})
            run.status = "error"
            return

        def scripted(proposal: Proposal, decision: bool) -> None:
            run.emit(
                "proposal",
                {"action": proposal.action, "lines": proposal.lines, "scripted": decision},
            )

        result = run_case(case, settings, on_event=run.trace, show_proposal=scripted)
        if result.result is None:
            run.emit("error", {"message": "; ".join(result.problems)})
            run.status = "error"
            return
        run.emit("answer", _answer(result.result, "replay", settings))
        run.status = "done"

    @app.post("/api/ask", dependencies=[Depends(require_app_header)])
    def ask(body: AskBody) -> dict[str, str]:
        run = Run(uuid.uuid4().hex)
        runs[run.id] = run
        while len(runs) > MAX_RUNS_KEPT:
            runs.pop(next(iter(runs)))
        worker = run_demo if demo else run_live
        threading.Thread(target=worker, args=(run, body), daemon=True).start()
        return {"run_id": run.id}

    @app.get("/api/runs/{run_id}")
    def poll(run_id: str, after: int = 0) -> dict[str, Any]:
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(404, "unknown run")
        events = run.since(after)
        return {"status": run.status, "events": events, "next": after + len(events)}

    @app.post("/api/runs/{run_id}/decision", dependencies=[Depends(require_app_header)])
    def decision(run_id: str, body: DecisionBody) -> dict[str, bool]:
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(404, "unknown run")
        if run.status != "waiting_approval":
            raise HTTPException(409, "this run is not waiting for approval")
        run.decide(body.approve)
        return {"ok": True}

    return app
