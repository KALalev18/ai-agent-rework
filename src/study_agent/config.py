"""Settings loaded from environment variables (and an optional .env file).

The calendar URLs contain private tokens. They are stored with ``repr=False`` so they never
show up in a traceback, a log line or anything sent to the model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = "claude-opus-5-5"
VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")


class ConfigError(Exception):
    """Raised when a setting is missing or malformed."""


def _parse_hhmm(value: str, name: str) -> time:
    try:
        hours, minutes = value.strip().split(":")
        return time(int(hours), int(minutes))
    except ValueError as exc:
        raise ConfigError(f"{name} must look like 09:00, got {value!r}") from exc


def _int(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from exc
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}, got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    # Either a local path or a private https URL. Never printed, logged or sent to the model.
    deadlines_source: str | None = field(default=None, repr=False)
    timetable_source: str | None = field(default=None, repr=False)
    model: str = DEFAULT_MODEL
    effort: str = "medium"
    max_rounds: int = 8
    day_start: time = time(9, 0)
    day_end: time = time(21, 0)
    daily_study_cap_min: int = 300
    max_block_min: int = 90
    sync_max_age_hours: int = 6
    eur_per_usd: float = 0.86

    @property
    def db_path(self) -> Path:
        return self.data_dir / "study.db"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"


def load_settings(env_file: Path | None = None) -> Settings:
    """Read settings from the environment. A .env file in the working directory is loaded first."""
    load_dotenv(env_file if env_file else Path.cwd() / ".env", override=False)

    effort = os.environ.get("STUDY_EFFORT", "medium").strip().lower()
    if effort not in VALID_EFFORTS:
        raise ConfigError(f"STUDY_EFFORT must be one of {', '.join(VALID_EFFORTS)}")

    day_start = _parse_hhmm(os.environ.get("STUDY_DAY_START", "09:00"), "STUDY_DAY_START")
    day_end = _parse_hhmm(os.environ.get("STUDY_DAY_END", "21:00"), "STUDY_DAY_END")
    if day_end <= day_start:
        raise ConfigError("STUDY_DAY_END must be later than STUDY_DAY_START")

    raw_rate = os.environ.get("STUDY_EUR_PER_USD", "0.86")
    try:
        eur_per_usd = float(raw_rate)
    except ValueError as exc:
        raise ConfigError(f"STUDY_EUR_PER_USD must be a number, got {raw_rate!r}") from exc

    data_dir = Path(os.environ.get("STUDY_DATA_DIR", "") or Path.home() / ".study-agent")

    return Settings(
        data_dir=data_dir.expanduser(),
        deadlines_source=os.environ.get("STUDY_DEADLINES_ICS") or None,
        timetable_source=os.environ.get("STUDY_TIMETABLE") or None,
        model=os.environ.get("STUDY_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        effort=effort,
        max_rounds=_int("STUDY_MAX_ROUNDS", 8, 1, 20),
        day_start=day_start,
        day_end=day_end,
        daily_study_cap_min=_int("STUDY_DAILY_CAP_MIN", 300, 30, 900),
        max_block_min=_int("STUDY_MAX_BLOCK_MIN", 90, 30, 240),
        sync_max_age_hours=_int("STUDY_SYNC_MAX_AGE_HOURS", 6, 0, 168),
        eur_per_usd=eur_per_usd,
    )
