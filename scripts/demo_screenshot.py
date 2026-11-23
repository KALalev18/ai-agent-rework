"""Regenerate docs/demo.svg from a real `study demo` run.

Usage: uv run python scripts/demo_screenshot.py
"""

from __future__ import annotations

from rich.console import Console

from study_agent import render

render.console = Console(record=True, width=100, force_terminal=True)

from study_agent import cli  # noqa: E402  (must import after swapping the console)

cli.console = render.console
cli.main(["demo"])
render.console.save_svg("docs/demo.svg", title="uv run study demo")
