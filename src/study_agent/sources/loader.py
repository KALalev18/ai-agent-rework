"""Load the deadline and timetable calendars from a local file or a private URL.

Private URLs are downloaded into a local cache. If a download fails, the last cached copy is
used and a warning is shown, so a flaky network never leaves me without a plan. Error messages
never include the URL (it contains a private token): exceptions are re-raised ``from None``.
"""

from __future__ import annotations

import time as _time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from study_agent.config import Settings
from study_agent.models import Deadline, Event
from study_agent.sources.ics import IcsError, parse_deadlines, parse_events
from study_agent.sources.timetable_yaml import TimetableError, parse_timetable_yaml
from study_agent.timeutil import Clock, system_clock


class SourceError(Exception):
    """A calendar could not be loaded. The message is safe to show (it never contains a URL)."""


def is_url(source: str) -> bool:
    return source.lower().startswith(("https://", "http://", "webcal://"))


def download(url: str, timeout: float = 20.0) -> bytes:
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://") :]
    request = urllib.request.Request(url, headers={"User-Agent": "study-agent/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data: bytes = response.read()
    except urllib.error.HTTPError as exc:
        raise SourceError(f"download failed (HTTP {exc.code})") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise SourceError("download failed (network error)") from None
    if not data.lstrip().startswith(b"BEGIN:VCALENDAR"):
        raise SourceError("the URL did not return a calendar (expired or wrong link?)") from None
    return data


@dataclass
class _Loaded:
    raw: bytes
    label: str
    kind: str  # "ics" or "yaml"


@dataclass
class Calendars:
    """Lazily loads both calendars once per process and remembers any warnings."""

    settings: Settings
    clock: Clock = system_clock
    offline: bool = False
    warnings: list[str] = field(default_factory=list)
    _deadlines: list[Deadline] | None = field(default=None, repr=False)
    _timetable: _Loaded | None = field(default=None, repr=False)
    _timetable_loaded: bool = field(default=False, repr=False)

    def _load(self, source: str, cache_name: str, url_label: str, force: bool) -> _Loaded:
        if not is_url(source):
            path = Path(source).expanduser()
            if not path.is_file():
                raise SourceError(f"calendar file not found: {path.name}")
            kind = "yaml" if path.suffix.lower() in (".yaml", ".yml") else "ics"
            return _Loaded(path.read_bytes(), path.name, kind)

        cache = self.settings.cache_dir / cache_name
        age_hours = (_time.time() - cache.stat().st_mtime) / 3600 if cache.exists() else None
        fresh = age_hours is not None and age_hours < self.settings.sync_max_age_hours
        if self.offline or (fresh and not force):
            if cache.exists():
                return _Loaded(cache.read_bytes(), url_label, "ics")
            raise SourceError(f"no cached copy of {url_label}; run `study sync` online first")
        try:
            data = download(source)
        except SourceError as exc:
            if cache.exists():
                self.warnings.append(
                    f"{url_label}: {exc}; using the cached copy from {age_hours:.0f} h ago"
                )
                return _Loaded(cache.read_bytes(), url_label, "ics")
            raise
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(cache)
        return _Loaded(data, url_label, "ics")

    def sync(self, force: bool = True) -> dict[str, int]:
        """Download both calendars now and report how many items each contains."""
        self._deadlines = None
        self._timetable_loaded = False
        counts = {"deadlines": len(self.deadlines(force=force))}
        if self._timetable_source(force=force) is not None:
            today = self.clock().date()
            counts["timetable_events_next_7_days"] = len(
                self.events(today, today + timedelta(days=6))
            )
        return counts

    def deadlines(self, force: bool = False) -> list[Deadline]:
        if self._deadlines is None:
            source = self.settings.deadlines_source
            if not source:
                self.warnings.append("STUDY_DEADLINES_ICS is not set: no deadlines loaded")
                self._deadlines = []
            else:
                loaded = self._load(source, "deadlines.ics", "mycourses.ics", force)
                try:
                    self._deadlines = parse_deadlines(loaded.raw, loaded.label)
                except IcsError as exc:
                    raise SourceError(f"{loaded.label}: {exc}") from None
        return self._deadlines

    def _timetable_source(self, force: bool = False) -> _Loaded | None:
        if not self._timetable_loaded:
            source = self.settings.timetable_source
            self._timetable = (
                self._load(source, "timetable.ics", "timetable.ics", force) if source else None
            )
            self._timetable_loaded = True
        return self._timetable

    def events(self, start: date, end: date) -> list[Event]:
        loaded = self._timetable_source()
        if loaded is None:
            return []
        try:
            if loaded.kind == "yaml":
                return parse_timetable_yaml(loaded.raw.decode("utf-8"), loaded.label, start, end)
            return parse_events(loaded.raw, loaded.label, start, end)
        except (IcsError, TimetableError) as exc:
            raise SourceError(f"{loaded.label}: {exc}") from None
