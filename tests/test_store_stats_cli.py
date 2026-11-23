from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from study_agent.cli import main
from study_agent.demo import DEADLINES, TIMETABLE
from study_agent.stats import compute_stats, percentile
from study_agent.store import RunRecord, Store, parse_ref
from study_agent.timeutil import TZ


def test_block_queries_are_correct_across_dst(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    now = datetime(2026, 10, 20, 9, 0, tzinfo=TZ)
    # 02:30 local on 25 Oct exists twice; store one block before and one after the change.
    before = datetime(2026, 10, 24, 23, 0, tzinfo=TZ)
    after = datetime(2026, 10, 25, 12, 0, tzinfo=TZ)
    store.add_block("x", "C", "after", after, after + timedelta(hours=1), now)
    store.add_block("x", "C", "before", before, before + timedelta(minutes=30), now)
    found = store.blocks_between(
        datetime(2026, 10, 24, tzinfo=TZ), datetime(2026, 10, 26, tzinfo=TZ)
    )
    assert [b.title for b in found] == ["before", "after"]
    assert found[1].start == after and found[1].start.utcoffset() == timedelta(hours=2)
    store.close()


def test_parse_ref() -> None:
    assert parse_ref("T-12", "T") == 12
    assert parse_ref("b-3", "B") == 3
    assert parse_ref("D-abc123", "T") is None


def test_percentile_nearest_rank() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert percentile(values, 50) == 5.0
    assert percentile(values, 95) == 10.0
    assert percentile([], 50) == 0.0


def test_stats_from_run_log(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    for day, latency, cost in ((23, 4000, 0.02), (23, 6000, 0.03), (24, 5000, 0.05)):
        store.record_run(
            RunRecord(
                "today",
                "live",
                "claude-opus-5-5",
                "q",
                "a",
                2,
                2,
                1000,
                500,
                3000,
                0,
                cost,
                latency,
                0,
                day == 24,
                "end_turn",
            ),
            datetime(2026, 11, day, 8, 0, tzinfo=TZ),
        )
    report = compute_stats(store.runs(mode="live"), eur_per_usd=1.0)
    assert report is not None
    assert report["days used"].startswith("2 ")
    assert report["cost per day used"] == "EUR 0.050"
    assert report["`study today` latency p50 / p95"].startswith("5.0 s / 6.0 s")
    assert report["input tokens served from cache"] == "75%"
    assert report["answers corrected by the grounding check"] == "1"
    assert compute_stats([], 1.0) is None
    store.close()


def test_cli_demo_runs_without_an_api_key(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "Proposed change: Log progress" in out
    assert "grounding check" in out


def test_cli_eval_replay_passes() -> None:
    assert main(["eval"]) == 0


def test_cli_deadlines_table_needs_no_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("STUDY_DEADLINES_ICS", str(DEADLINES))
    monkeypatch.setenv("STUDY_TIMETABLE", str(TIMETABLE))
    assert main(["deadlines", "--days", "400"]) == 0
    assert "Final exam" in capsys.readouterr().out


def test_cli_reports_config_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUDY_EFFORT", "nonsense")
    assert main(["stats"]) == 2


def test_cli_stats_without_runs(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["stats"]) == 0
    assert "No live runs" in capsys.readouterr().out


def test_cli_record_requires_live() -> None:
    assert main(["eval", "--record"]) == 2
