"""Calendar loading. The private calendar URL must never leak into errors, reprs or results."""

from __future__ import annotations

import io
import os
import time
import urllib.error
from dataclasses import replace
from pathlib import Path

import pytest

from study_agent.config import ConfigError, Settings, load_settings
from study_agent.demo import DEADLINES
from study_agent.sources import loader
from study_agent.sources.loader import Calendars, SourceError

SECRET = "https://mycourses.example/calendar/export_execute.php?userid=1&authtoken=FAKE_TOKEN"


def fake_urlopen(payload: bytes | Exception):  # type: ignore[no-untyped-def]
    def opener(request, timeout=0):  # type: ignore[no-untyped-def]
        if isinstance(payload, Exception):
            raise payload
        return io.BytesIO(payload)

    return opener


def url_settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, deadlines_source=SECRET)


def test_settings_repr_hides_calendar_sources(tmp_path: Path) -> None:
    assert "FAKE_TOKEN" not in repr(url_settings(tmp_path))


def test_download_is_cached_and_labelled_without_the_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loader.urllib.request, "urlopen", fake_urlopen(DEADLINES.read_bytes()))
    deadlines = Calendars(url_settings(tmp_path)).deadlines()
    assert deadlines and {d.source for d in deadlines} == {"mycourses.ics"}
    assert (tmp_path / "cache" / "deadlines.ics").exists()
    assert all("FAKE_TOKEN" not in str(d.to_dict()) for d in deadlines)


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.HTTPError(SECRET, 403, "Forbidden", None, None),  # type: ignore[arg-type]
        urllib.error.URLError(f"cannot reach {SECRET}"),
    ],
)
def test_download_errors_never_contain_the_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    monkeypatch.setattr(loader.urllib.request, "urlopen", fake_urlopen(failure))
    with pytest.raises(SourceError) as info:
        Calendars(url_settings(tmp_path)).deadlines()
    assert "FAKE_TOKEN" not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__


def test_non_calendar_response_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader.urllib.request, "urlopen", fake_urlopen(b"<html>login</html>"))
    with pytest.raises(SourceError, match="did not return a calendar"):
        Calendars(url_settings(tmp_path)).deadlines()


def test_failed_download_falls_back_to_stale_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache" / "deadlines.ics"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(DEADLINES.read_bytes())
    old = time.time() - 48 * 3600
    os.utime(cache, (old, old))
    monkeypatch.setattr(loader.urllib.request, "urlopen", fake_urlopen(urllib.error.URLError("x")))
    calendars = Calendars(url_settings(tmp_path))
    assert calendars.deadlines()
    assert "cached copy" in calendars.warnings[0]
    assert "FAKE_TOKEN" not in calendars.warnings[0]


def test_fresh_cache_skips_the_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache" / "deadlines.ics"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(DEADLINES.read_bytes())
    monkeypatch.setattr(loader.urllib.request, "urlopen", fake_urlopen(AssertionError("network")))
    assert Calendars(url_settings(tmp_path)).deadlines()


def test_offline_without_cache_explains_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="study sync"):
        Calendars(url_settings(tmp_path), offline=True).deadlines()


def test_missing_sources(tmp_path: Path) -> None:
    calendars = Calendars(Settings(data_dir=tmp_path))
    assert calendars.deadlines() == []
    assert "STUDY_DEADLINES_ICS" in calendars.warnings[0]
    with pytest.raises(SourceError, match="not found"):
        Calendars(replace(Settings(data_dir=tmp_path), deadlines_source="nope.ics")).deadlines()


def test_load_settings_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUDY_EFFORT", "extreme")
    with pytest.raises(ConfigError):
        load_settings()
    monkeypatch.setenv("STUDY_EFFORT", "low")
    monkeypatch.setenv("STUDY_DAY_START", "22:00")
    with pytest.raises(ConfigError):
        load_settings()
    monkeypatch.setenv("STUDY_DAY_START", "08:30")
    monkeypatch.setenv("STUDY_MAX_ROUNDS", "50")
    with pytest.raises(ConfigError):
        load_settings()
    monkeypatch.setenv("STUDY_MAX_ROUNDS", "5")
    settings = load_settings()
    assert (settings.effort, settings.max_rounds, settings.day_start.hour) == ("low", 5, 8)
