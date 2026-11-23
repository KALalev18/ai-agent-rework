"""Local web app: dashboard + chat on top of the same agent, tools and approval gate as the CLI."""

from study_agent.web.app import create_app

__all__ = ["create_app"]
