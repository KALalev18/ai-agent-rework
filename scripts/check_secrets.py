"""Fail if a file contains something that looks like a secret: an API key or a private calendar URL.

Runs as a pre-commit hook (on staged files) and in CI (on every tracked file).
Usage: python scripts/check_secrets.py FILE...
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PATTERNS = {
    "Anthropic API key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "private calendar token": re.compile(r"authtoken=[A-Za-z0-9]{8,}", re.IGNORECASE),
    "MyCourses calendar export URL": re.compile(r"mycourses\.aalto\.fi/calendar/export", re.I),
}
SKIP_SUFFIXES = {".png", ".gif", ".jpg", ".svg", ".lock"}


def scan(path: Path) -> list[str]:
    if path.suffix in SKIP_SUFFIXES or path.name == "check_secrets.py" or not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    return [f"{path}: {name}" for name, pattern in PATTERNS.items() if pattern.search(text)]


def main(paths: list[str]) -> int:
    problems = [problem for p in paths for problem in scan(Path(p))]
    for problem in problems:
        print(f"possible secret: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
