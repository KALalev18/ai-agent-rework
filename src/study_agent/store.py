"""SQLite storage: my tasks, deadline state, progress log, study blocks, and the run log.

Times are stored as UTC ISO strings, so SQL range queries compare correctly even when the
stored values were created on different sides of a DST change (+03:00 vs +02:00 would not
sort as text). They are converted back to Helsinki time when read.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path

from study_agent.timeutil import to_local

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    course TEXT,
    due TEXT,
    estimate_min INTEGER,
    deadline_id TEXT,
    created_at TEXT NOT NULL,
    done_at TEXT
);
CREATE TABLE IF NOT EXISTS deadline_state (
    deadline_id TEXT PRIMARY KEY,
    done_at TEXT,
    estimate_min INTEGER
);
CREATE TABLE IF NOT EXISTS progress (
    id INTEGER PRIMARY KEY,
    logged_at TEXT NOT NULL,
    text TEXT NOT NULL,
    item_id TEXT,
    course TEXT,
    minutes INTEGER
);
CREATE TABLE IF NOT EXISTS blocks (
    id INTEGER PRIMARY KEY,
    item_id TEXT NOT NULL,
    course TEXT NOT NULL,
    title TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    command TEXT NOT NULL,
    mode TEXT NOT NULL,
    model TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT,
    rounds INTEGER NOT NULL,
    tool_calls INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL,
    cache_write_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    latency_ms INTEGER NOT NULL,
    ungrounded INTEGER NOT NULL,
    corrected INTEGER NOT NULL,
    stop_reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    name TEXT NOT NULL,
    input_json TEXT NOT NULL,
    ok INTEGER NOT NULL,
    approved INTEGER,
    latency_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS courses (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS course_deadlines (
    id INTEGER PRIMARY KEY,
    course TEXT NOT NULL REFERENCES courses(code) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    due TEXT NOT NULL,
    created_at TEXT NOT NULL,
    weight_pct INTEGER
);
CREATE TABLE IF NOT EXISTS course_sessions (
    id INTEGER PRIMARY KEY,
    course TEXT NOT NULL REFERENCES courses(code) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    weekday INTEGER NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    first_day TEXT NOT NULL,
    last_day TEXT NOT NULL
);
"""


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def _local(value: str | None) -> datetime | None:
    return to_local(datetime.fromisoformat(value)) if value else None


def task_ref(task_id: int) -> str:
    return f"T-{task_id}"


def block_ref(block_id: int) -> str:
    return f"B-{block_id}"


def deadline_ref(deadline_id: int) -> str:
    return f"M-{deadline_id}"


def parse_ref(ref: str, prefix: str) -> int | None:
    """'T-12' -> 12 for prefix 'T'. Returns None if ``ref`` is not of that kind."""
    head, _, tail = ref.strip().upper().partition("-")
    if head == prefix and tail.isdigit():
        return int(tail)
    return None


@dataclass(frozen=True)
class Task:
    id: int
    title: str
    course: str | None
    due: datetime | None
    estimate_min: int | None
    deadline_id: str | None
    created_at: datetime
    done_at: datetime | None

    @property
    def ref(self) -> str:
        return task_ref(self.id)


@dataclass(frozen=True)
class Block:
    id: int
    item_id: str
    course: str
    title: str
    start: datetime
    end: datetime

    @property
    def ref(self) -> str:
        return block_ref(self.id)


@dataclass(frozen=True)
class Progress:
    id: int
    logged_at: datetime
    text: str
    item_id: str | None
    course: str | None
    minutes: int | None


@dataclass(frozen=True)
class DeadlineState:
    done_at: datetime | None
    estimate_min: int | None


@dataclass(frozen=True)
class Course:
    code: str
    name: str


@dataclass(frozen=True)
class CourseDeadline:
    id: int
    course: str
    kind: str
    title: str
    due: datetime
    weight_pct: int | None = None

    @property
    def ref(self) -> str:
        return deadline_ref(self.id)


@dataclass(frozen=True)
class Session:
    id: int
    course: str
    kind: str
    weekday: int  # 0 = Monday
    start: time
    end: time
    first_day: date
    last_day: date


@dataclass(frozen=True)
class RunRecord:
    command: str
    mode: str
    model: str
    question: str
    answer: str
    rounds: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    latency_ms: int
    ungrounded: int
    corrected: bool
    stop_reason: str


class Store:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after a database was created (SQLite has no IF NOT EXISTS)."""
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(course_deadlines)")}
        if "weight_pct" not in columns:
            with self.conn:
                self.conn.execute("ALTER TABLE course_deadlines ADD COLUMN weight_pct INTEGER")

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.conn:
            yield self.conn

    # --- tasks -----------------------------------------------------------------------------

    def add_task(
        self,
        title: str,
        now: datetime,
        course: str | None = None,
        due: datetime | None = None,
        estimate_min: int | None = None,
        deadline_id: str | None = None,
    ) -> int:
        with self.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO tasks (title, course, due, estimate_min, deadline_id, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (title, course, _utc(due) if due else None, estimate_min, deadline_id, _utc(now)),
            )
        return int(cursor.lastrowid or 0)

    def _task(self, row: sqlite3.Row) -> Task:
        created = _local(row["created_at"])
        assert created is not None
        return Task(
            row["id"],
            row["title"],
            row["course"],
            _local(row["due"]),
            row["estimate_min"],
            row["deadline_id"],
            created,
            _local(row["done_at"]),
        )

    def tasks(self, include_done: bool = False) -> list[Task]:
        query = "SELECT * FROM tasks"
        if not include_done:
            query += " WHERE done_at IS NULL"
        rows = self.conn.execute(query + " ORDER BY due IS NULL, due, id").fetchall()
        return [self._task(row) for row in rows]

    def task(self, task_id: int) -> Task | None:
        row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._task(row) if row else None

    def complete_task(self, task_id: int, now: datetime) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE tasks SET done_at = ? WHERE id = ?", (_utc(now), task_id))

    def set_task_estimate(self, task_id: int, minutes: int) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE tasks SET estimate_min = ? WHERE id = ?", (minutes, task_id))

    # --- deadlines (the calendar is read-only; my state about each deadline lives here) ------

    def deadline_states(self) -> dict[str, DeadlineState]:
        rows = self.conn.execute("SELECT * FROM deadline_state").fetchall()
        return {
            row["deadline_id"]: DeadlineState(_local(row["done_at"]), row["estimate_min"])
            for row in rows
        }

    def set_deadline_done(self, deadline_id: str, now: datetime) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO deadline_state (deadline_id, done_at) VALUES (?, ?)"
                " ON CONFLICT(deadline_id) DO UPDATE SET done_at = excluded.done_at",
                (deadline_id, _utc(now)),
            )

    def set_deadline_estimate(self, deadline_id: str, minutes: int) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO deadline_state (deadline_id, estimate_min) VALUES (?, ?)"
                " ON CONFLICT(deadline_id) DO UPDATE SET estimate_min = excluded.estimate_min",
                (deadline_id, minutes),
            )

    # --- progress ----------------------------------------------------------------------------

    def log_progress(
        self,
        text: str,
        now: datetime,
        item_id: str | None = None,
        course: str | None = None,
        minutes: int | None = None,
    ) -> int:
        with self.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO progress (logged_at, text, item_id, course, minutes)"
                " VALUES (?, ?, ?, ?, ?)",
                (_utc(now), text, item_id, course, minutes),
            )
        return int(cursor.lastrowid or 0)

    def progress_between(self, start: datetime, end: datetime) -> list[Progress]:
        rows = self.conn.execute(
            "SELECT * FROM progress WHERE logged_at >= ? AND logged_at < ? ORDER BY logged_at",
            (_utc(start), _utc(end)),
        ).fetchall()
        return [self._progress(row) for row in rows]

    def _progress(self, row: sqlite3.Row) -> Progress:
        logged = _local(row["logged_at"])
        assert logged is not None
        return Progress(
            row["id"], logged, row["text"], row["item_id"], row["course"], row["minutes"]
        )

    def progress_for(self, item_id: str) -> list[Progress]:
        rows = self.conn.execute(
            "SELECT * FROM progress WHERE item_id = ? ORDER BY logged_at", (item_id,)
        ).fetchall()
        return [self._progress(row) for row in rows]

    def logged_minutes(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT item_id, SUM(minutes) AS total FROM progress"
            " WHERE item_id IS NOT NULL AND minutes IS NOT NULL GROUP BY item_id"
        ).fetchall()
        return {row["item_id"]: int(row["total"]) for row in rows}

    # --- study blocks ------------------------------------------------------------------------

    def add_block(
        self, item_id: str, course: str, title: str, start: datetime, end: datetime, now: datetime
    ) -> int:
        with self.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO blocks (item_id, course, title, start, end, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (item_id, course, title, _utc(start), _utc(end), _utc(now)),
            )
        return int(cursor.lastrowid or 0)

    def _block(self, row: sqlite3.Row) -> Block:
        start, end = _local(row["start"]), _local(row["end"])
        assert start is not None and end is not None
        return Block(row["id"], row["item_id"], row["course"], row["title"], start, end)

    def blocks_between(self, start: datetime, end: datetime) -> list[Block]:
        rows = self.conn.execute(
            "SELECT * FROM blocks WHERE end > ? AND start < ? ORDER BY start",
            (_utc(start), _utc(end)),
        ).fetchall()
        return [self._block(row) for row in rows]

    def block(self, block_id: int) -> Block | None:
        row = self.conn.execute("SELECT * FROM blocks WHERE id = ?", (block_id,)).fetchone()
        return self._block(row) if row else None

    def blocks_for(self, item_id: str) -> list[Block]:
        rows = self.conn.execute(
            "SELECT * FROM blocks WHERE item_id = ? ORDER BY start", (item_id,)
        ).fetchall()
        return [self._block(row) for row in rows]

    def delete_block(self, block_id: int) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM blocks WHERE id = ?", (block_id,))

    def move_block(self, block_id: int, start: datetime, end: datetime) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE blocks SET start = ?, end = ? WHERE id = ?",
                (_utc(start), _utc(end), block_id),
            )

    # --- run log -----------------------------------------------------------------------------

    def record_run(self, run: RunRecord, started_at: datetime) -> int:
        with self.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO runs (started_at, command, mode, model, question, answer, rounds,"
                " tool_calls, input_tokens, output_tokens, cache_read_tokens,"
                " cache_write_tokens, cost_usd, latency_ms, ungrounded, corrected, stop_reason)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _utc(started_at),
                    run.command,
                    run.mode,
                    run.model,
                    run.question,
                    run.answer,
                    run.rounds,
                    run.tool_calls,
                    run.input_tokens,
                    run.output_tokens,
                    run.cache_read_tokens,
                    run.cache_write_tokens,
                    run.cost_usd,
                    run.latency_ms,
                    run.ungrounded,
                    int(run.corrected),
                    run.stop_reason,
                ),
            )
        return int(cursor.lastrowid or 0)

    def record_tool_call(
        self,
        run_id: int,
        name: str,
        tool_input: dict[str, object],
        ok: bool,
        approved: bool | None,
        latency_ms: int,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO tool_calls (run_id, name, input_json, ok, approved, latency_ms)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    name,
                    json.dumps(tool_input, sort_keys=True),
                    int(ok),
                    None if approved is None else int(approved),
                    latency_ms,
                ),
            )

    def runs(self, mode: str | None = None) -> list[sqlite3.Row]:
        query = "SELECT * FROM runs"
        params: tuple[str, ...] = ()
        if mode:
            query, params = query + " WHERE mode = ?", (mode,)
        return self.conn.execute(query + " ORDER BY started_at", params).fetchall()

    # --- courses I enter myself -------------------------------------------------------------
    # Deadlines I type in get ids "M-<n>". Their done-state and estimates live in deadline_state,
    # exactly like calendar deadlines, so every other feature treats them the same way.

    def add_course(self, code: str, name: str, now: datetime) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO courses (code, name, created_at) VALUES (?, ?, ?)",
                (code, name, _utc(now)),
            )

    def rename_course(self, code: str, name: str) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE courses SET name = ? WHERE code = ?", (name, code))

    def courses(self) -> list[Course]:
        rows = self.conn.execute("SELECT code, name FROM courses ORDER BY code").fetchall()
        return [Course(row["code"], row["name"]) for row in rows]

    def course(self, code: str) -> Course | None:
        row = self.conn.execute("SELECT code, name FROM courses WHERE code = ?", (code,)).fetchone()
        return Course(row["code"], row["name"]) if row else None

    def delete_course(self, code: str) -> None:
        ids = [deadline_ref(d.id) for d in self.course_deadlines() if d.course == code]
        with self.transaction() as conn:
            self._forget_items(conn, ids)
            conn.execute("DELETE FROM courses WHERE code = ?", (code,))

    def _forget_items(self, conn: sqlite3.Connection, item_ids: list[str]) -> None:
        for item_id in item_ids:
            conn.execute("DELETE FROM deadline_state WHERE deadline_id = ?", (item_id,))
            conn.execute("DELETE FROM blocks WHERE item_id = ?", (item_id,))

    def add_course_deadline(
        self,
        course: str,
        kind: str,
        title: str,
        due: datetime,
        now: datetime,
        weight_pct: int | None = None,
    ) -> int:
        with self.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO course_deadlines (course, kind, title, due, created_at, weight_pct)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (course, kind, title, _utc(due), _utc(now), weight_pct),
            )
        return int(cursor.lastrowid or 0)

    def update_course_deadline(
        self,
        deadline_id: int,
        kind: str,
        title: str,
        due: datetime,
        weight_pct: int | None = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE course_deadlines SET kind = ?, title = ?, due = ?, weight_pct = ?"
                " WHERE id = ?",
                (kind, title, _utc(due), weight_pct, deadline_id),
            )

    def delete_course_deadline(self, deadline_id: int) -> None:
        with self.transaction() as conn:
            self._forget_items(conn, [deadline_ref(deadline_id)])
            conn.execute("DELETE FROM course_deadlines WHERE id = ?", (deadline_id,))

    def course_deadlines(self) -> list[CourseDeadline]:
        rows = self.conn.execute("SELECT * FROM course_deadlines ORDER BY due, id").fetchall()
        result = []
        for row in rows:
            due = _local(row["due"])
            assert due is not None
            result.append(
                CourseDeadline(
                    row["id"], row["course"], row["kind"], row["title"], due, row["weight_pct"]
                )
            )
        return result

    def add_session(
        self,
        course: str,
        kind: str,
        weekday: int,
        start: time,
        end: time,
        first_day: date,
        last_day: date,
    ) -> int:
        with self.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO course_sessions (course, kind, weekday, start, end, first_day,"
                " last_day) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    course,
                    kind,
                    weekday,
                    start.strftime("%H:%M"),
                    end.strftime("%H:%M"),
                    first_day.isoformat(),
                    last_day.isoformat(),
                ),
            )
        return int(cursor.lastrowid or 0)

    def delete_session(self, session_id: int) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM course_sessions WHERE id = ?", (session_id,))

    def sessions(self) -> list[Session]:
        rows = self.conn.execute(
            "SELECT * FROM course_sessions ORDER BY course, weekday, start"
        ).fetchall()
        return [
            Session(
                row["id"],
                row["course"],
                row["kind"],
                row["weekday"],
                time.fromisoformat(row["start"]),
                time.fromisoformat(row["end"]),
                date.fromisoformat(row["first_day"]),
                date.fromisoformat(row["last_day"]),
            )
            for row in rows
        ]
