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
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class LlmUnavailable(RuntimeError):
    """Raised when no language model is configured or reachable."""


@dataclass
class LlmClient:
    """Thin OpenAI-compatible chat client."""

    settings: Settings

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

    def complete_json(
        self, system: str, user: str, timeout: float = 120.0
    ) -> dict[str, Any] | None:
        """Ask for a JSON object; return ``None`` if unavailable or unparseable.

        Free-tier providers differ in how strictly they support
        ``response_format``, so a rejection triggers one retry without it.
        """
        if not self.configured:
            logger.info("No LLM configured; skipping generation.")
            return None

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        payload: dict[str, Any] = {
            "model": self.settings.research_model,
            "messages": messages,
            "temperature": 0.2,
        }

        for attempt in range(2):
            body = dict(payload)
            if attempt == 0:
                body["response_format"] = {"type": "json_object"}
            try:
                response = httpx.post(
                    self._endpoint(),
                    json=body,
                    headers=self._headers(),
                    timeout=timeout,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                return _parse_json(content)
            except Exception as exc:  # noqa: BLE001 - degrade gracefully
                if attempt == 0:
                    logger.info(
                        "Retrying without response_format after: %s",
                        str(exc)[:160],
                    )
                    continue
                logger.warning("LLM call failed: %s", str(exc)[:200])
        return None



    def complete_text(
            self, system: str, user: str, timeout: float = 90.0, max_chars: int = 400
        ) -> str | None:
            """Ask for a short plain-text answer.

            Used for one-line summaries, where demanding JSON makes small models
            fail (they answer in prose). Returns ``None`` if unavailable.
            """
            if not self.configured:
                logger.info("No LLM configured; skipping generation.")
                return None
            try:
                response = httpx.post(
                    self._endpoint(),
                    json={
                        "model": self.settings.research_model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "temperature": 0.2,
                    },
                    headers=self._headers(),
                    timeout=timeout,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"] or ""
                content = content.strip()
                return content[:max_chars] if content else None
            except Exception as exc:  # noqa: BLE001 - degrade gracefully
                logger.warning("LLM text call failed: %s", str(exc)[:200])
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