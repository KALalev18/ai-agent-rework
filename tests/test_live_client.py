"""The live client, tested offline: a mock HTTP transport checks the exact request we send."""

from __future__ import annotations

import json
from typing import Any

import anthropic
import httpx2
import pytest

from study_agent.llm import FALLBACK_BETA, AnthropicModel, LLMError

RESPONSE = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-opus-5-5",
    "content": [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "tool_use", "id": "toolu_1", "name": "plan_day", "input": {}},
    ],
    "stop_reason": "tool_use",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 120,
        "output_tokens": 40,
        "cache_read_input_tokens": 2400,
        "cache_creation_input_tokens": 0,
    },
}


def model_with(handler: Any, record: list[dict[str, Any]] | None = None) -> AnthropicModel:
    model = AnthropicModel.__new__(AnthropicModel)
    model.client = anthropic.Anthropic(
        api_key="test-key",
        base_url="http://api.test",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    model.record_to = record
    return model


def call(model: AnthropicModel, name: str = "claude-opus-5-5") -> Any:
    return model.create(
        model=name,
        max_tokens=1000,
        system="SYSTEM",
        tools=[{"name": "plan_day", "description": "d", "input_schema": {"type": "object"}}],
        messages=[{"role": "user", "content": "hi"}],
        effort="medium",
    )


def test_request_shape_caching_effort_and_fallback() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta", "")
        return httpx2.Response(200, json=RESPONSE)

    recorded: list[dict[str, Any]] = []
    response = call(model_with(handler, recorded))
    body = seen["body"]
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["output_config"] == {"effort": "medium"}
    assert body["fallbacks"] == "default"
    assert FALLBACK_BETA in seen["beta"]
    assert "thinking" not in body and "temperature" not in body

    assert response.stop_reason == "tool_use"
    assert response.usage.cache_read == 2400
    assert response.content[0]["signature"] == "sig"  # thinking blocks are kept for replay
    assert recorded[0]["stop_reason"] == "tool_use"


def test_models_without_fallback_support_do_not_send_it() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json={**RESPONSE, "model": "claude-haiku-5-5"})

    call(model_with(handler), name="claude-haiku-5-5")
    assert "fallbacks" not in seen["body"]


def test_missing_credentials_give_a_clear_message(monkeypatch: pytest.MonkeyPatch) -> None:
    model = model_with(lambda request: httpx2.Response(200, json=RESPONSE))

    def no_credentials(**kwargs: Any) -> None:
        # What the SDK raises when it finds no API key, token or profile at all.
        raise TypeError('"Could not resolve authentication method. Expected one of api_key"')

    monkeypatch.setattr(model.client.beta.messages, "create", no_credentials)
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        call(model)


def test_broken_credential_profile_gives_a_clear_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(tmp_path / "empty"))
    with pytest.raises(LLMError, match="credentials"):
        AnthropicModel()


@pytest.mark.parametrize(
    ("status", "message"),
    [(401, "API key"), (429, "rate limited"), (500, "Claude API error 500")],
)
def test_api_errors_become_readable_messages(status: int, message: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        error = {"type": "error", "error": {"type": "x", "message": "nope"}}
        return httpx2.Response(status, json=error)

    with pytest.raises(LLMError, match=message):
        call(model_with(handler))
