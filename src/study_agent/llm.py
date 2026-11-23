"""Model clients: the live Claude API, and replay of recorded or scripted responses.

The agent loop only depends on the small ``ModelClient`` protocol, so tests, the demo and CI can
swap the real API for a replay client and run without an API key or network access.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import anthropic
from anthropic import omit
from anthropic.types.beta import (
    BetaMessageParam,
    BetaOutputConfigParam,
    BetaTextBlockParam,
    BetaToolParam,
)

JSON = dict[str, Any]

# USD per million tokens: input, output, cache write (5-minute TTL, 1.25x input), cache read.
# From Anthropic's pricing page as of Nov 2026. Update here if prices change.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
    "claude-fable-5-2": (8.00, 40.00, 10.00, 0.20),
}

# Models that accept the server-side refusal fallback (`fallbacks: "default"`).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-fable-5-2", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(Exception):
    """A model call failed. The message is safe to show."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def __iadd__(self, other: Usage) -> Usage:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read += other.cache_read
        self.cache_write += other.cache_write
        return self

    @classmethod
    def from_dict(cls, data: JSON | None) -> Usage:
        data = data or {}
        return cls(
            int(data.get("input_tokens") or 0),
            int(data.get("output_tokens") or 0),
            int(data.get("cache_read_input_tokens") or 0),
            int(data.get("cache_creation_input_tokens") or 0),
        )


def cost_usd(model: str, usage: Usage) -> float | None:
    prices = PRICES.get(model)
    if prices is None:
        return None
    p_in, p_out, p_write, p_read = prices
    return (
        usage.input_tokens * p_in
        + usage.output_tokens * p_out
        + usage.cache_write * p_write
        + usage.cache_read * p_read
    ) / 1_000_000


@dataclass
class ModelResponse:
    content: list[JSON]
    stop_reason: str
    usage: Usage
    model: str

    @classmethod
    def from_dict(cls, data: JSON) -> ModelResponse:
        return cls(
            list(data["content"]),
            str(data.get("stop_reason") or "end_turn"),
            Usage.from_dict(data.get("usage")),
            str(data.get("model") or "replay"),
        )


class ModelClient(Protocol):
    mode: str

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        tools: list[JSON],
        messages: list[JSON],
        effort: str,
    ) -> ModelResponse: ...


class AnthropicModel:
    """The real Claude API. Optionally records every response for later replay."""

    mode = "live"

    def __init__(self, record_to: list[JSON] | None = None) -> None:
        self.client = anthropic.Anthropic()
        self.record_to = record_to

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        tools: list[JSON],
        messages: list[JSON],
        effort: str,
    ) -> ModelResponse:
        # If a safety classifier declines, the API retries on a fallback model in the same
        # call instead of returning an empty refusal. Only some models accept this.
        fallback = model in FALLBACK_MODELS
        # Breakpoint 1: tools + system prompt, identical on every run.
        system_blocks: list[BetaTextBlockParam] = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]
        try:
            message = self.client.beta.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_blocks,
                tools=cast("list[BetaToolParam]", tools),
                messages=cast("list[BetaMessageParam]", messages),
                output_config=cast("BetaOutputConfigParam", {"effort": effort}),
                # Breakpoint 2 (automatic): the growing conversation inside one run, so each
                # tool round re-reads the earlier rounds from cache.
                cache_control={"type": "ephemeral"},
                betas=[FALLBACK_BETA] if fallback else omit,
                fallbacks="default" if fallback else omit,
            )
        except anthropic.AuthenticationError:
            raise LLMError("the API key is missing or invalid (set ANTHROPIC_API_KEY)") from None
        except anthropic.RateLimitError:
            raise LLMError("rate limited by the API; try again in a minute") from None
        except anthropic.APIConnectionError:
            raise LLMError("could not reach the Claude API (network error)") from None
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Claude API error {exc.status_code}: {exc.message}") from None
        except anthropic.AnthropicError as exc:
            raise LLMError(f"Claude API client error: {exc}") from None
        data = message.to_dict(mode="json")
        if self.record_to is not None:
            self.record_to.append(
                {k: data.get(k) for k in ("content", "stop_reason", "usage", "model")}
            )
        return ModelResponse.from_dict(data)


class ReplayExhausted(LLMError):
    """The agent asked for more model turns than the recording contains."""


@dataclass
class ReplayModel:
    """Serves pre-recorded responses in order. Records the requests it receives for tests."""

    responses: Sequence[JSON]
    mode: str = "replay"
    requests: list[JSON] = field(default_factory=list)
    _next: int = 0

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        tools: list[JSON],
        messages: list[JSON],
        effort: str,
    ) -> ModelResponse:
        self.requests.append({"model": model, "messages": json.loads(json.dumps(messages))})
        if self._next >= len(self.responses):
            raise ReplayExhausted("the recording has no more model responses for this run")
        response = ModelResponse.from_dict(self.responses[self._next])
        self._next += 1
        return response


def script_to_responses(script: Sequence[JSON]) -> list[JSON]:
    """Turn a compact scripted conversation into API-shaped responses.

    Each step is either ``{"tools": [{"name": ..., "input": {...}}], "say": "optional"}`` (one
    assistant turn with tool calls) or ``{"text": "..."}`` (a final answer).
    """
    responses: list[JSON] = []
    counter = 0
    for step in script:
        if "tools" in step:
            content: list[JSON] = []
            if step.get("say"):
                content.append({"type": "text", "text": step["say"]})
            for call in step["tools"]:
                counter += 1
                content.append(
                    {
                        "type": "tool_use",
                        "id": f"toolu_scripted_{counter:03d}",
                        "name": call["name"],
                        "input": call.get("input", {}),
                    }
                )
            responses.append({"content": content, "stop_reason": "tool_use"})
        else:
            responses.append(
                {"content": [{"type": "text", "text": step["text"]}], "stop_reason": "end_turn"}
            )
    return responses


def load_cassette(path: Path) -> list[JSON]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data["responses"])


def save_cassette(path: Path, responses: list[JSON], meta: JSON) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, "responses": responses}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
