"""FastAPI backend for the local web app.

Design:
- Every page reads through the same deterministic tools the agent uses, so the UI, the CLI and
  the agent always agree. No model call is needed to show data.
- Every change goes through a write tool's Proposal. From a button, the UI first asks
  /preview (validation + the proposal text), shows it, and only then calls the action. The
  click is the approval. From the assistant, the agent thread blocks until I click Approve or
  Decline. Either way the same validation runs (no overlaps, nothing after the deadline).
- The assistant runs the Agent in a background thread; the browser polls /api/runs/{id} for
  events. Polling is simpler than WebSockets or SSE and is plenty for one user on localhost.

Security for a local app: it binds to 127.0.0.1, only accepts localhost Host headers (blocks DNS
rebinding), and every POST needs an `X-Study-Agent` header. Browsers cannot add custom headers
to cross-site requests without a CORS preflight, which this app never allows, so another website
cannot start runs or apply changes through my browser.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import tempfile
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from study_agent import courses as course_data
from study_agent.agent import Agent, AgentResult
from study_agent.config import Settings
from study_agent.demo import demo_clock, demo_settings, seed
from study_agent.evals import load_cases, run_case, summarize
from study_agent.llm import AnthropicModel, LLMError, ModelClient
from study_agent.planner import suggest_blocks
from study_agent.render import footer
from study_agent.runlog import log_run
from study_agent.sources.loader import Calendars, SourceError, is_url
from study_agent.stats import compute_stats
from study_agent.store import Block, Store, parse_ref
from study_agent.timeutil import (
    Clock,
    day_bounds,
    fmt_day,
    fmt_moment,
    iso,
    minutes_between,
    monday_of,
    parse_day,
    system_clock,
)
from study_agent.tools import (
    TOOLS,
    Proposal,
    ToolContext,
    execute,
    get_progress,
    item_dict,
    plan_day,
)

STATIC_DIR = Path(__file__).parent / "static"
APPROVAL_TIMEOUT_S = 600
MAX_RUNS_KEPT = 50
WRITE_TOOLS = {tool.name for tool in TOOLS if tool.writes}
ToolArgs = Annotated[dict[str, Any] | None, Body()]
log = logging.getLogger(__name__)


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=64)


class DecisionBody(BaseModel):
    approve: bool


class CourseBody(BaseModel):
    code: str = Field(default="", max_length=40)
    name: str = Field(default="", max_length=200)


class DeadlineBody(BaseModel):
    kind: str = "assignment"
    title: str = Field(max_length=400)
    due: str = Field(max_length=40)
    estimate_min: int | None = Field(default=None, ge=5, le=6000)


class SessionBody(BaseModel):
    kind: str = "lecture"
    weekday: int = Field(ge=0, le=6)
    start: clock_time
    end: clock_time
    first_day: date
    last_day: date


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


def _block_dict(block: Block) -> dict[str, Any]:
    return {
        "block_id": block.ref,
        "item_id": block.item_id,
        "course": block.course,
        "title": block.title,
        "start": iso(block.start),
        "end": iso(block.end),
        "when": f"{fmt_moment(block.start)}-{block.end:%H:%M}",
        "minutes": minutes_between(block.start, block.end),
    }


def _source_label(source: str | None) -> str:
    if not source:
        return "not set"
    return "private URL (hidden)" if is_url(source) else Path(source).name


def create_app(
    settings: Settings,
    demo: bool = False,
    client_factory: Callable[[], ModelClient] = AnthropicModel,
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost"),
) -> FastAPI:
    clock: Clock = system_clock
    demo_cases = {c.question: c for c in load_cases()} if demo else {}
    if demo:
        # A seeded copy of the fictional semester. Changes made with buttons persist in it for
        # the session; assistant answers are scripted replays on fresh copies.
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
    calendars_lock = threading.Lock()
    shared_calendars: dict[str, tuple[Calendars, float]] = {}

    def require_app_header(x_study_agent: str | None = Header(default=None)) -> None:
        if x_study_agent != "1":
            raise HTTPException(403, "missing X-Study-Agent header")

    def calendars(fresh: bool = False) -> Calendars:
        # Parsed calendars are reused across requests and rebuilt every hour (the loader itself
        # only re-downloads when its cache is older than STUDY_SYNC_MAX_AGE_HOURS).
        with calendars_lock:
            cached = shared_calendars.get("c")
            if fresh or cached is None or time.monotonic() - cached[1] > 3600:
                cached = (Calendars(settings, clock), time.monotonic())
                shared_calendars["c"] = cached
            return cached[0]

    def open_context() -> tuple[Store, ToolContext]:
        # One SQLite connection per request/thread: sqlite3 objects must stay in their thread.
        store = Store(settings.db_path)
        return store, ToolContext(settings, store, calendars(), clock)

    def with_context(fn: Callable[[Store, ToolContext], Any]) -> Any:
        store, ctx = open_context()
        try:
            return fn(store, ctx)
        except SourceError as exc:
            raise HTTPException(503, str(exc)) from None
        finally:
            store.close()

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    # --- read endpoints -----------------------------------------------------------------------

    @app.get("/api/overview")
    def overview() -> dict[str, Any]:
        def build(store: Store, ctx: ToolContext) -> dict[str, Any]:
            now = ctx.now()
            items = ctx.items()
            risks = ctx.risks(items)
            today = plan_day(ctx, {})
            week_end = now + timedelta(days=7)
            coming = sorted(
                (i for i in items if i.due and now <= i.due <= week_end), key=lambda i: i.due or now
            )
            logged = [p for p in store.progress_between(now - timedelta(days=7), now) if p.minutes]
            done_week = sum(
                1
                for s in store.deadline_states().values()
                if s.done_at and s.done_at >= now - timedelta(days=7)
            ) + sum(
                1
                for t in store.tasks(include_done=True)
                if t.done_at and t.done_at >= now - timedelta(days=7)
            )
            return {
                "mode": "demo" if demo else "live",
                "model": settings.model,
                "now": fmt_moment(now),
                "now_iso": iso(now),
                "today": today,
                "coming_up": [item_dict(i, risks.get(i.id)) for i in coming],
                "stats": {
                    "due_this_week": len(coming),
                    "at_risk": sum(1 for r in risks.values() if r.status in ("at_risk", "overdue")),
                    "tight": sum(1 for r in risks.values() if r.status == "tight"),
                    "planned_today_min": sum(b["minutes"] for b in today["suggested_blocks"])
                    + sum(
                        minutes_between(
                            datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"])
                        )
                        for b in today["saved_blocks"]
                    ),
                    "logged_week_min": sum(p.minutes or 0 for p in logged),
                    "done_week": done_week,
                },
                # "No calendar URL set" is not a problem when I type my courses in myself.
                "warnings": [w for w in ctx.calendars.warnings if "is not set" not in w],
                "demo_questions": list(demo_cases),
                "courses": sorted(
                    {c.code for c in store.courses()} | {d.course for d in ctx.deadlines()}
                ),
                "setup": {
                    "courses": len(store.courses()),
                    "calendar_connected": bool(settings.deadlines_source),
                    "has_data": bool(items or ctx.deadlines()),
                },
            }

        result: dict[str, Any] = with_context(build)
        return result

    @app.get("/api/items")
    def items(include_done: bool = False) -> dict[str, Any]:
        def build(store: Store, ctx: ToolContext) -> dict[str, Any]:
            open_items = ctx.items()
            risks = ctx.risks(open_items)
            far = ctx.now() + timedelta(days=3650)
            ordered = sorted(open_items, key=lambda i: (i.due or far, i.id))
            done: list[dict[str, Any]] = []
            if include_done:
                states = store.deadline_states()
                for deadline in ctx.deadlines():
                    state = states.get(deadline.id)
                    if state and state.done_at:
                        done.append(
                            {
                                **deadline.to_dict(),
                                "kind": "deadline",
                                "done_at": fmt_moment(state.done_at),
                            }
                        )
                for task in store.tasks(include_done=True):
                    if task.done_at:
                        done.append(
                            {
                                "id": task.ref,
                                "kind": "task",
                                "course": task.course or "-",
                                "title": task.title,
                                "done_at": fmt_moment(task.done_at),
                            }
                        )
            courses = sorted({i.course for i in open_items} | {d["course"] for d in done})
            return {
                "now": iso(ctx.now()),
                "items": [item_dict(i, risks.get(i.id)) for i in ordered],
                "done": done,
                "courses": courses,
            }

        result: dict[str, Any] = with_context(build)
        return result

    @app.get("/api/items/{item_id}")
    def item_detail(item_id: str) -> dict[str, Any]:
        def build(store: Store, ctx: ToolContext) -> dict[str, Any]:
            open_items = ctx.items()
            risks = ctx.risks(open_items)
            item = next((i for i in open_items if i.id.upper() == item_id.upper()), None)
            if item is None:
                raise HTTPException(404, "no open deadline or task with that id")
            return {
                "item": item_dict(item, risks.get(item.id)),
                "progress": [
                    {"logged": fmt_moment(p.logged_at), "text": p.text, "minutes": p.minutes}
                    for p in store.progress_for(item.id)
                ],
                "blocks": [_block_dict(b) for b in store.blocks_for(item.id)],
            }

        result: dict[str, Any] = with_context(build)
        return result

    @app.get("/api/week")
    def week(start: str | None = None) -> dict[str, Any]:
        def build(store: Store, ctx: ToolContext) -> dict[str, Any]:
            try:
                first = monday_of(parse_day(start, ctx.today()))
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            days = [first + timedelta(days=n) for n in range(7)]
            lo, _ = day_bounds(days[0])
            _, hi = day_bounds(days[-1])
            now = ctx.now()
            events = ctx.events(days[0], days[-1])
            open_items = ctx.items()
            risks = ctx.risks(open_items)
            future_saved = store.blocks_between(now, now + timedelta(days=120))
            upcoming = [d for d in days if d >= ctx.today()]
            suggested = suggest_blocks(upcoming, open_items, events, future_saved, now, settings)
            return {
                "start": days[0].isoformat(),
                "label": f"{fmt_day(days[0])} - {fmt_day(days[-1])}",
                "today": ctx.today().isoformat(),
                "now": iso(now),
                "window": [f"{settings.day_start:%H:%M}", f"{settings.day_end:%H:%M}"],
                "days": [{"date": d.isoformat(), "label": fmt_day(d)} for d in days],
                "events": [e.to_dict() for e in events],
                "saved": [_block_dict(b) for b in store.blocks_between(lo, hi)],
                "suggested": [
                    {
                        "item_id": s.item_id,
                        "course": s.course,
                        "title": s.title,
                        "start": iso(s.start),
                        "end": iso(s.end),
                        "minutes": s.minutes,
                    }
                    for s in suggested
                ],
                "deadlines": [
                    item_dict(i, risks.get(i.id)) for i in open_items if i.due and lo <= i.due < hi
                ],
            }

        result: dict[str, Any] = with_context(build)
        return result

    @app.get("/api/progress")
    def progress(days: int = 14) -> dict[str, Any]:
        def build(store: Store, ctx: ToolContext) -> dict[str, Any]:
            span = max(1, min(days, 120))
            start = ctx.today() - timedelta(days=span - 1)
            data = get_progress(ctx, {"from_date": start.isoformat(), "to_date": "today"})
            lo, _ = day_bounds(start)
            per_day: dict[str, int] = {
                (start + timedelta(days=n)).isoformat(): 0 for n in range(span)
            }
            per_course: dict[str, int] = defaultdict(int)
            for p in store.progress_between(lo, ctx.now() + timedelta(days=1)):
                if p.minutes:
                    per_day[p.logged_at.date().isoformat()] = (
                        per_day.get(p.logged_at.date().isoformat(), 0) + p.minutes
                    )
                    per_course[p.course or "other"] += p.minutes
            return {
                **data,
                "per_day": [
                    {"date": d, "label": fmt_day(datetime.fromisoformat(d).date()), "minutes": m}
                    for d, m in per_day.items()
                ],
                "per_course": dict(sorted(per_course.items(), key=lambda kv: -kv[1])),
            }

        result: dict[str, Any] = with_context(build)
        return result

    @app.get("/api/stats")
    def stats() -> dict[str, Any]:
        store = Store(settings.db_path)
        try:
            rows = store.runs(mode="live")
            report = compute_stats(rows, settings.eur_per_usd)
            recent = [
                {
                    "when": fmt_moment(datetime.fromisoformat(r["started_at"])),
                    "command": r["command"],
                    "question": r["question"],
                    "tool_calls": r["tool_calls"],
                    "latency_s": round(r["latency_ms"] / 1000, 1),
                    "cost_eur": round(r["cost_usd"] * settings.eur_per_usd, 4),
                    "grounded": not r["ungrounded"],
                    "corrected": bool(r["corrected"]),
                }
                for r in reversed(rows[-20:])
            ]
        finally:
            store.close()
        return {"report": report, "recent": recent}

    @app.get("/api/settings")
    def settings_view() -> dict[str, Any]:
        return {
            "mode": "demo" if demo else "live",
            "model": settings.model,
            "effort": settings.effort,
            "max_rounds": settings.max_rounds,
            "study_window": f"{settings.day_start:%H:%M}-{settings.day_end:%H:%M}",
            "daily_cap_min": settings.daily_study_cap_min,
            "max_block_min": settings.max_block_min,
            "deadlines_source": _source_label(settings.deadlines_source),
            "timetable_source": _source_label(settings.timetable_source),
            "sync_max_age_hours": settings.sync_max_age_hours,
            "data_dir": str(settings.data_dir),
            "tools": [{"name": t.name, "writes": t.writes} for t in TOOLS],
        }

    # --- changes from buttons: preview (validation + proposal), then apply --------------------

    def _run_action(name: str, args: dict[str, Any], apply: bool) -> dict[str, Any]:
        if name not in WRITE_TOOLS:
            raise HTTPException(404, f"unknown action {name}")
        captured: list[Proposal] = []

        def decide(proposal: Proposal) -> bool:
            captured.append(proposal)
            return apply  # preview: validate and describe, never write

        def run(store: Store, ctx: ToolContext) -> dict[str, Any]:
            return json.loads(execute(name, args, ctx, decide).content)  # type: ignore[no-any-return]

        payload: dict[str, Any] = with_context(run)
        if "error" in payload:
            raise HTTPException(400, payload["error"])
        if not apply:
            proposal = captured[0]
            return {"action": proposal.action, "lines": proposal.lines}
        return payload

    @app.post("/api/actions/{name}/preview", dependencies=[Depends(require_app_header)])
    def preview(name: str, args: ToolArgs = None) -> dict[str, Any]:
        return _run_action(name, args or {}, apply=False)

    @app.post("/api/actions/{name}", dependencies=[Depends(require_app_header)])
    def action(name: str, args: ToolArgs = None) -> dict[str, Any]:
        return _run_action(name, args or {}, apply=True)

    @app.post("/api/sync", dependencies=[Depends(require_app_header)])
    def sync() -> dict[str, Any]:
        if demo:
            raise HTTPException(400, "demo mode uses bundled calendars; nothing to sync")
        fresh = calendars(fresh=True)
        try:
            counts = fresh.sync(force=True)
        except SourceError as exc:
            raise HTTPException(503, str(exc)) from None
        return {"counts": counts, "warnings": fresh.warnings}

    @app.post("/api/eval", dependencies=[Depends(require_app_header)])
    def run_eval() -> dict[str, Any]:
        results = [run_case(case, settings) for case in load_cases()]
        return {
            "summary": summarize(results),
            "cases": [
                {
                    "id": r.case.id,
                    "question": r.case.question,
                    "passed": r.passed,
                    "source": r.source,
                    "tools": [c.name for c in r.result.tool_calls] if r.result else [],
                    "caught": r.caught,
                    "problems": r.problems,
                }
                for r in results
            ],
        }

    # --- courses I type in -----------------------------------------------------------------------

    def course_edit(fn: Callable[[Store], Any]) -> Any:
        """Run a course change; bad input becomes a 400 with a readable message."""
        store = Store(settings.db_path)
        try:
            return fn(store)
        except course_data.CourseInputError as exc:
            raise HTTPException(400, str(exc)) from None
        except sqlite3.IntegrityError:
            raise HTTPException(409, "that course already exists") from None
        finally:
            store.close()

    @app.get("/api/courses")
    def list_courses() -> dict[str, Any]:
        def build(store: Store, ctx: ToolContext) -> dict[str, Any]:
            open_items = ctx.items()
            risks = ctx.risks(open_items)
            by_id = {i.id: i for i in open_items}
            states = store.deadline_states()
            mine = store.course_deadlines()
            sessions = store.sessions()
            result = []
            for course in store.courses():
                rows = []
                for d in (x for x in mine if x.course == course.code):
                    item = by_id.get(d.ref)
                    state = states.get(d.ref)
                    rows.append(
                        {
                            "id": d.ref,
                            "kind": d.kind,
                            "title": d.title,
                            "due": iso(d.due),
                            "due_local": fmt_moment(d.due),
                            "done": bool(state and state.done_at),
                            "risk": risks[d.ref].status if d.ref in risks else None,
                            "estimate_min": item.estimate_min if item else None,
                            "remaining_min": item.remaining_min if item else 0,
                        }
                    )
                result.append(
                    {
                        "code": course.code,
                        "name": course.name,
                        "deadlines": rows,
                        "sessions": [
                            {
                                "id": x.id,
                                "kind": x.kind,
                                "weekday": x.weekday,
                                "start": x.start.strftime("%H:%M"),
                                "end": x.end.strftime("%H:%M"),
                                "first_day": x.first_day.isoformat(),
                                "last_day": x.last_day.isoformat(),
                            }
                            for x in sessions
                            if x.course == course.code
                        ],
                    }
                )
            entered = {c.code for c in store.courses()}
            imported = sorted({d.course for d in ctx.calendars.deadlines()} - entered)
            return {
                "courses": result,
                "imported_courses": imported,
                "kinds": list(course_data.KINDS),
                "session_kinds": list(course_data.SESSION_KINDS),
                "today": ctx.today().isoformat(),
            }

        result: dict[str, Any] = with_context(build)
        return result

    @app.post("/api/courses", dependencies=[Depends(require_app_header)])
    def create_course(body: CourseBody) -> dict[str, str]:
        def run(store: Store) -> dict[str, str]:
            code = course_data.clean_code(body.code)
            name = course_data.clean_text(body.name or code, "name", 120)
            store.add_course(code, name, clock())
            return {"code": code}

        result: dict[str, str] = course_edit(run)
        return result

    @app.patch("/api/courses/{code}", dependencies=[Depends(require_app_header)])
    def rename_course(code: str, body: CourseBody) -> dict[str, bool]:
        def run(store: Store) -> dict[str, bool]:
            if store.course(code) is None:
                raise HTTPException(404, "unknown course")
            store.rename_course(code, course_data.clean_text(body.name or code, "name", 120))
            return {"ok": True}

        result: dict[str, bool] = course_edit(run)
        return result

    @app.delete("/api/courses/{code}", dependencies=[Depends(require_app_header)])
    def delete_course(code: str) -> dict[str, bool]:
        def run(store: Store) -> dict[str, bool]:
            if store.course(code) is None:
                raise HTTPException(404, "unknown course")
            store.delete_course(code)
            return {"ok": True}

        result: dict[str, bool] = course_edit(run)
        return result

    def _deadline_fields(body: DeadlineBody) -> tuple[str, str, datetime]:
        return (
            course_data.clean_kind(body.kind),
            course_data.clean_text(body.title, "title"),
            course_data.parse_due(body.due),
        )

    @app.post("/api/courses/{code}/deadlines", dependencies=[Depends(require_app_header)])
    def create_deadline(code: str, body: DeadlineBody) -> dict[str, str]:
        def run(store: Store) -> dict[str, str]:
            if store.course(code) is None:
                raise HTTPException(404, "unknown course")
            kind, title, due = _deadline_fields(body)
            ref = f"M-{store.add_course_deadline(code, kind, title, due, clock())}"
            if body.estimate_min:
                store.set_deadline_estimate(ref, body.estimate_min)
            return {"id": ref}

        result: dict[str, str] = course_edit(run)
        return result

    def _my_deadline(store: Store, ref: str) -> int:
        number = parse_ref(ref, "M")
        if number is None or not any(d.id == number for d in store.course_deadlines()):
            raise HTTPException(404, "unknown deadline")
        return number

    @app.patch("/api/deadlines/{ref}", dependencies=[Depends(require_app_header)])
    def edit_deadline(ref: str, body: DeadlineBody) -> dict[str, bool]:
        def run(store: Store) -> dict[str, bool]:
            number = _my_deadline(store, ref)
            store.update_course_deadline(number, *_deadline_fields(body))
            if body.estimate_min:
                store.set_deadline_estimate(ref.upper(), body.estimate_min)
            return {"ok": True}

        result: dict[str, bool] = course_edit(run)
        return result

    @app.delete("/api/deadlines/{ref}", dependencies=[Depends(require_app_header)])
    def remove_deadline(ref: str) -> dict[str, bool]:
        def run(store: Store) -> dict[str, bool]:
            store.delete_course_deadline(_my_deadline(store, ref))
            return {"ok": True}

        result: dict[str, bool] = course_edit(run)
        return result

    @app.post("/api/courses/{code}/sessions", dependencies=[Depends(require_app_header)])
    def create_session(code: str, body: SessionBody) -> dict[str, int]:
        def run(store: Store) -> dict[str, int]:
            if store.course(code) is None:
                raise HTTPException(404, "unknown course")
            kind = course_data.clean_kind(body.kind, course_data.SESSION_KINDS)
            if body.end <= body.start:
                raise course_data.CourseInputError("a class must end after it starts")
            if body.last_day < body.first_day:
                raise course_data.CourseInputError("the last week is before the first week")
            new_id = store.add_session(
                code, kind, body.weekday, body.start, body.end, body.first_day, body.last_day
            )
            return {"id": new_id}

        result: dict[str, int] = course_edit(run)
        return result

    @app.delete("/api/sessions/{session_id}", dependencies=[Depends(require_app_header)])
    def remove_session(session_id: int) -> dict[str, bool]:
        def run(store: Store) -> dict[str, bool]:
            store.delete_session(session_id)
            return {"ok": True}

        result: dict[str, bool] = course_edit(run)
        return result

    @app.get("/api/calendar")
    def month(month: str | None = None) -> dict[str, Any]:
        """Everything in a month grid: deadlines (open and done), classes and study blocks."""

        def build(store: Store, ctx: ToolContext) -> dict[str, Any]:
            try:
                first = date.fromisoformat(f"{month}-01") if month else ctx.today().replace(day=1)
            except ValueError:
                raise HTTPException(400, "month must look like 2026-11") from None
            grid_start = monday_of(first)
            next_month = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
            grid_end = monday_of(next_month - timedelta(days=1)) + timedelta(days=6)
            lo, _ = day_bounds(grid_start)
            _, hi = day_bounds(grid_end)
            open_items = ctx.items()
            risks = ctx.risks(open_items)
            open_ids = {i.id for i in open_items}
            deadlines = [
                {
                    **d.to_dict(),
                    "done": d.id not in open_ids,
                    "risk": risks[d.id].status if d.id in risks else None,
                }
                for d in ctx.deadlines()
                if lo <= d.due < hi
            ]
            return {
                "month": first.strftime("%Y-%m"),
                "label": first.strftime("%B %Y"),
                "today": ctx.today().isoformat(),
                "grid_start": grid_start.isoformat(),
                "grid_end": grid_end.isoformat(),
                "deadlines": deadlines,
                "events": [e.to_dict() for e in ctx.events(grid_start, grid_end)],
                "blocks": [_block_dict(b) for b in store.blocks_between(lo, hi)],
                "courses": sorted(
                    {str(d["course"]) for d in deadlines} | {c.code for c in store.courses()}
                ),
            }

        result: dict[str, Any] = with_context(build)
        return result

    # --- the assistant ------------------------------------------------------------------------

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
