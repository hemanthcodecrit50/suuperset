"""Tests for tolerant LLM response handling.

Free-tier providers vary in `response_format` support and in how cleanly they
return JSON, so the client must degrade rather than crash.
"""

from __future__ import annotations

import httpx
import pytest

from app.services.llm import LlmClient, _parse_json
from app.config import Settings


def _response(status: int, payload: dict) -> httpx.Response:
    """Build a response with a request bound (raise_for_status needs one)."""
    return httpx.Response(status, json=payload, request=httpx.Request("POST", "https://x"))


# --- JSON parsing ------------------------------------------------------

def test_parse_plain_json():
    assert _parse_json('{"summary": "ok"}') == {"summary": "ok"}


def test_parse_json_in_markdown_fence():
    raw = '```json\n{"summary": "fenced"}\n```'
    assert _parse_json(raw) == {"summary": "fenced"}


def test_parse_json_with_surrounding_prose():
    raw = 'Here you go:\n{"summary": "embedded"}\nHope that helps.'
    assert _parse_json(raw) == {"summary": "embedded"}


def test_parse_invalid_returns_none():
    assert _parse_json("no json here") is None
    assert _parse_json("") is None


# --- client behaviour --------------------------------------------------

def _client(**overrides) -> LlmClient:
    settings = Settings(
        openai_api_key="test-key",
        openai_base_url="https://example.invalid/v1",
        research_model="test-model",
        **overrides,
    )
    return LlmClient(settings=settings)


def test_not_configured_without_key_or_base_url():
    settings = Settings(openai_api_key="", openai_base_url="")
    assert LlmClient(settings=settings).configured is False
    assert LlmClient(settings=settings).complete_json("s", "u") is None


def test_endpoint_is_built_from_base_url():
    client = _client()
    assert client._endpoint() == "https://example.invalid/v1/chat/completions"


def test_retries_without_response_format_when_rejected(monkeypatch):
    """A 400 on response_format must retry rather than give up."""
    client = _client()
    calls: list[dict] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json)
        if "response_format" in json:
            return _response(400, {"error": {"message": "response_format unsupported"}})
        return _response(
            200, {"choices": [{"message": {"content": '{"summary": "second try"}'}}]}
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    result = client.complete_json("system", "user")

    assert result == {"summary": "second try"}
    assert len(calls) == 2
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


def test_gives_up_after_second_failure(monkeypatch):
    client = _client()
    calls: list[int] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(1)
        return _response(401, {"error": {"message": "bad key"}})

    monkeypatch.setattr(httpx, "post", fake_post)
    assert client.complete_json("system", "user") is None
    assert len(calls) == 2


def test_authorization_header_only_when_key_present():
    assert "Authorization" in _client()._headers()
    settings = Settings(openai_api_key="", openai_base_url="http://localhost:11434/v1")
    assert "Authorization" not in LlmClient(settings=settings)._headers()

# --- plain text generation --------------------------------------------

def test_complete_text_returns_raw_content(monkeypatch):
    client = _client()
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(json)
        return _response(200, {"choices": [{"message": {"content": "A short line."}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    assert client.complete_text("sys", "usr") == "A short line."
    # No JSON mode: small models answer in prose.
    assert "response_format" not in captured


def test_complete_text_returns_none_on_failure(monkeypatch):
    client = _client()

    def fake_post(url, json=None, headers=None, timeout=None):
        return _response(500, {"error": {"message": "boom"}})

    monkeypatch.setattr(httpx, "post", fake_post)
    assert client.complete_text("sys", "usr") is None


def test_complete_text_truncates(monkeypatch):
    client = _client()

    def fake_post(url, json=None, headers=None, timeout=None):
        return _response(200, {"choices": [{"message": {"content": "x" * 500}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    assert len(client.complete_text("sys", "usr")) == 400
