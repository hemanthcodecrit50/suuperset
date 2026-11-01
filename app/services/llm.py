"""LLM client for report/resume generation.

Works with any OpenAI-compatible endpoint, which includes local Ollama — so
this works on a free tier without any API key. If nothing is configured, the
callers fall back to safe, evidence-only output instead of guessing.

Rules enforced by the callers (not the model):
- Only claim what the supplied evidence supports.
- Always attach a source URL to any claim.
- Never invent metrics, technologies or achievements.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.services.events import EventLog
from app.services.llm_budget import LlmBudget

logger = logging.getLogger(__name__)


class LlmUnavailable(RuntimeError):
    """Raised when no language model is configured or reachable."""


@dataclass
class LlmClient:
    """Thin OpenAI-compatible chat client."""

    settings: Settings
    events: EventLog | None = None
    run_id: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.settings.openai_api_key or self.settings.openai_base_url)

    def _endpoint(self) -> str:
        base = (self.settings.openai_base_url or "").rstrip("/")
        if not base:
            base = "https://api.openai.com/v1"
        return f"{base}/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = self.settings.openai_api_key
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    @property
    def budget(self) -> LlmBudget:
        return LlmBudget(
            Path(self.settings.llm_usage_file),
            self.settings.llm_daily_request_limit,
            self.settings.llm_min_interval_seconds,
        )

    def complete_json(
        self, system: str, user: str, timeout: float = 120.0
    ) -> dict[str, Any] | None:
        """Ask for a JSON object; return ``None`` if unavailable or unparseable."""
        if not self.configured:
            logger.info("No LLM configured; skipping generation.")
            return None
        body = self._body(system, user)
        response = self._send({**body, "response_format": {"type": "json_object"}}, timeout)
        if response is not None and response.status_code == 400:
            # Some providers reject response_format; this is the only retry we allow
            response = self._send(body, timeout)
        return _parse_json(_content(response) or "")

    def complete_text(
        self, system: str, user: str, timeout: float = 90.0, max_chars: int = 400
    ) -> str | None:
        """Ask for a short plain-text answer; ``None`` if unavailable."""
        if not self.configured:
            logger.info("No LLM configured; skipping generation.")
            return None
        content = (_content(self._send(self._body(system, user), timeout)) or "").strip()
        return content[:max_chars] if content else None

    def _body(self, system: str, user: str) -> dict[str, Any]:
        return {
            "model": self.settings.research_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }

    def _send(self, body: dict[str, Any], timeout: float) -> httpx.Response | None:
        # Every HTTP request counts against the daily cap, including retries
        if not self.budget.acquire():
            logger.warning(
                "Daily model request cap (%d) reached; using evidence-only output.",
                self.settings.llm_daily_request_limit,
            )
            self._record(body, "skipped: daily cap reached", 0)
            return None
        started = time.monotonic()
        try:
            response = httpx.post(
                self._endpoint(), json=body, headers=self._headers(), timeout=timeout
            )
        except httpx.HTTPError as exc:
            logger.warning("LLM request failed: %s", str(exc)[:200])
            self._record(body, "network error", time.monotonic() - started)
            return None
        self._record(body, f"HTTP {response.status_code}", time.monotonic() - started)
        return response

    def _record(self, body: dict[str, Any], outcome: str, seconds: float) -> None:
        if self.events is None:
            return
        purpose = body["messages"][0]["content"].strip().splitlines()[0][:80]
        usage = self.budget.usage()
        self.events.record(
            "ai",
            "request",
            f"{purpose} · {outcome} · {usage.used}/{usage.limit} today",
            run_id=self.run_id,
            duration_ms=round(seconds * 1000),
            payload={"model": self.settings.research_model, "outcome": outcome},
        )


def _content(response: httpx.Response | None) -> str | None:
    if response is None:
        return None
    if response.status_code != 200:
        logger.warning("LLM returned %s: %s", response.status_code, response.text[:200])
        return None
    try:
        return response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        logger.warning("LLM response had no message content.")
        return None


def _parse_json(content: str) -> dict[str, Any] | None:
    """Parse model output as JSON, tolerating markdown fences and prose."""
    if not content:
        return None
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the first balanced-looking JSON object in the response.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def default_client() -> LlmClient:
    return LlmClient(settings=get_settings())


REPORT_SYSTEM = """You produce concise, factual company research briefs.

Hard rules:
- Use ONLY the evidence provided. Never add facts from memory.
- For every claim, include the source URL it came from.
- If evidence is missing for a section, set that field to null.
- Do not invent revenue, headcount, funding amounts or technologies.
- Mark uncertainty explicitly as "unknown".

Respond with a JSON object:
{
  "summary": "2-3 sentence overview or null",
  "overview": "...or null",
  "products": "...or null",
  "recent_developments": "...or null",
  "engineering_culture": "...or null",
  "role_responsibilities": "...or null",
  "compensation": "...or null",
  "concerns": "...or null",
  "recommendation": "one actionable sentence or null"
}"""

BULLET_SYSTEM = """You write resume bullet points that are strictly grounded
in the provided project evidence.

Hard rules:
- Use ONLY facts present in the project record. Never invent a metric,
  technology, employer or achievement.
- If the record does not support a claim, say so instead of inventing it.
- Keep each bullet to one line, past tense, outcome-oriented.

Respond with a JSON object:
{"bullet": "one grounded bullet, or null if unsupported"}"""