"""Free, terms-compliant evidence gathering.

Uses DuckDuckGo's public Instant Answer API (no key, no scraping of result
pages). It returns an abstract plus related topics for many company queries, and
degrades to nothing rather than inventing facts. Swap in a different backend by
implementing the ``SearchBackend`` protocol.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app.services.research import Evidence, SearchBackend

logger = logging.getLogger(__name__)

DUCKDUCKGO_API = "https://api.duckduckgo.com/"


@dataclass
class DuckDuckGoBackend(SearchBackend):
    """DuckDuckGo Instant Answer backend.

    The endpoint throttles aggressively (it answers ``202`` when rate limited),
    so requests are spaced out and retried with a short backoff.
    """

    timeout: float = 15.0
    max_results: int = 3
    delay_seconds: float = 1.5
    max_attempts: int = 3

    def search(self, query: str, limit: int | None = None) -> list[Evidence]:
        limit = limit or self.max_results
        params = urlencode({"q": query, "format": "json", "no_html": 1, "skip_disambig": 1})

        for attempt in range(1, self.max_attempts + 1):
            try:
                response = httpx.get(
                    f"{DUCKDUCKGO_API}?{params}",
                    timeout=self.timeout,
                    headers={"User-Agent": "placement-intelligence/0.1 (+personal use)"},
                )
                if response.status_code == 202:
                    # Throttled: back off and retry.
                    wait = self.delay_seconds * attempt
                    logger.debug("Search throttled for %r; waiting %.1fs", query, wait)
                    time.sleep(wait)
                    continue
                response.raise_for_status()
                payload: Any = response.json()
            except Exception as exc:  # noqa: BLE001 - research must never break collection
                logger.debug("Search backend failed for %r: %s", query, exc)
                time.sleep(self.delay_seconds * attempt)
                continue

            results = _to_evidence(payload, query, limit)
            if results:
                logger.debug("Collected %d evidence item(s) for %r", len(results), query)
            else:
                logger.debug("No evidence for %r (query returned nothing)", query)
            # Space out the next query either way.
            time.sleep(self.delay_seconds)
            return results

        logger.debug("Gave up on %r after %d attempts", query, self.max_attempts)
        return []


def _to_evidence(payload: dict, query: str, limit: int) -> list[Evidence]:
    results: list[Evidence] = []

    abstract = payload.get("AbstractText") or payload.get("Answer")
    abstract_url = payload.get("AbstractURL") or payload.get("AnswerURL")
    if abstract:
        results.append(
            Evidence(
                claim=" ".join(str(abstract).split())[:600],
                source_url=abstract_url or "https://duckduckgo.com",
                source_type="abstract",
            )
        )

    for topic in (payload.get("RelatedTopics") or [])[:limit]:
        text = topic.get("Text")
        url = topic.get("FirstURL")
        if not text:
            # Nested topic list
            for nested in topic.get("Topics", [])[:limit]:
                if nested.get("Text"):
                    results.append(
                        Evidence(
                            claim=" ".join(str(nested["Text"]).split())[:400],
                            source_url=nested.get("FirstURL") or "https://duckduckgo.com",
                            source_type="related",
                        )
                    )
            continue
        results.append(
            Evidence(
                claim=" ".join(str(text).split())[:400],
                source_url=url or "https://duckduckgo.com",
                source_type="related",
            )
        )
        if len(results) >= limit + 1:
            break

    logger.debug("Collected %d evidence item(s) for %r", len(results), query)
    return results