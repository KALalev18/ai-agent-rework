from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from study_agent.config import Settings
from study_agent.demo import demo_clock, demo_settings, seed
from study_agent.sources.loader import Calendars
from study_agent.store import Store
from study_agent.tools import ToolContext


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return demo_settings(tmp_path)


@pytest.fixture
def store(settings: Settings) -> Iterator[Store]:
    db = Store(settings.db_path)
    seed(db)
    yield db
    db.close()


@pytest.fixture
def ctx(settings: Settings, store: Store) -> ToolContext:
    return ToolContext(settings, store, Calendars(settings, demo_clock), demo_clock)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Tests never read my real .env, calendars or API key."""
    for name in (
        "ANTHROPIC_API_KEY",
        "STUDY_DEADLINES_ICS",
        "STUDY_TIMETABLE",
        "STUDY_MODEL",
        "STUDY_EFFORT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STUDY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)
