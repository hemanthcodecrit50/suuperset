"""Free, terms-compliant evidence gathering.

The week-1 upgrade replaces the single DuckDuckGo Instant Answer lookup with a
small composite backend:

- **Wikipedia / Wikidata** — encyclopedic company overview plus the official
  website (P856), both from public APIs built for programmatic access.
- **GDELT** — recent news articles with outlet, date and URL. Reputable outlets
  are ranked first; other outlets are kept but labelled as lower trust.
- **Brave Search** — optional, only when ``BRAVE_SEARCH_API_KEY`` is set.
- **DuckDuckGo Instant Answer** — keyless fallback for when the others return
  nothing.

Everything is cached on disk (7 days by default) and every result carries a URL,
so the critic can fetch the page and verify the claim. If a backend fails, the
others still run; a total failure returns no evidence rather than invented facts.
"""

from __future__ import annotations

import datetime as dt
import html
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import quote, urlparse

import httpx

from app.config import Settings, get_settings
from app.services.cache import FileCache
from app.services.research import Evidence, SearchBackend

logger = logging.getLogger(__name__)

DUCKDUCKGO_API = "https://api.duckduckgo.com/"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKIPEDIA_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
GDELT_API = "https://api.gdeltproject.org/api/v2/doc/doc"
BRAVE_API = "https://api.search.brave.com/res/v1/web/search"

# Outlets that usually report corporate news with editorial standards.
REPUTABLE_NEWS_DOMAINS = {
    "reuters.com",
    "bloomberg.com",
    "ft.com",
    "wsj.com",
    "economist.com",
    "techcrunch.com",
    "theverge.com",
    "wired.com",
    "business-standard.com",
    "economictimes.indiatimes.com",
    "livemint.com",
    "thehindu.com",
    "indianexpress.com",
    "moneycontrol.com",
    "yourstory.com",
    "inc42.com",
    "timesofindia.indiatimes.com",
}

_TRUST_RANK = {"official": 0, "reference": 1, "news": 2, "web": 3, "news_low": 4}


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value or "")
    return " ".join(html.unescape(text).split())


def _get_json(
    url: str,
    *,
    params: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
    client: httpx.Client | None = None,
) -> Any | None:
    for attempt in range(2):
        try:
            if client is not None:
                response = client.get(url, params=params, headers=headers, timeout=timeout)
            else:
                response = httpx.get(url, params=params, headers=headers, timeout=timeout)
            if response.status_code in (429, 503) and attempt == 0:
                retry_after = response.headers.get("retry-after")
                try:
                    wait = min(float(retry_after), 10.0) if retry_after else 2.0
                except ValueError:
                    wait = 2.0
                logger.debug("Search backend returned %s; retrying in %.1fs", response.status_code, wait)
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 - a backend failure must not stop research
            logger.debug("Search request failed (%s): %s", url, exc)
            return None
    return None


# GDELT asks for at most one request every 5 seconds. The throttle is
# process-wide so several queries in one run still stay inside that budget.
_GDELT_LAST_CALL = 0.0
_GDELT_LOCK = threading.Lock()


def _throttle_gdelt(seconds: float) -> None:
    global _GDELT_LAST_CALL
    if seconds <= 0:
        return
    # The lock keeps parallel researcher threads from all firing at once
    with _GDELT_LOCK:
        wait = seconds - (time.monotonic() - _GDELT_LAST_CALL)
        if wait > 0:
            time.sleep(wait)
        _GDELT_LAST_CALL = time.monotonic()


@dataclass
class DuckDuckGoBackend(SearchBackend):
    """DuckDuckGo Instant Answer fallback (no key, no result-page scraping)."""

    timeout: float = 15.0
    max_results: int = 3
    delay_seconds: float = 1.5
    max_attempts: int = 3
    user_agent: str = "placement-intelligence/0.1 (contact: local@example.invalid)"
    client: httpx.Client | None = None

    def search(self, query: str, limit: int | None = None) -> list[Evidence]:
        limit = limit or self.max_results
        params = {
            "q": query,
            "format": "json",
            "no_html": 1,
            "skip_disambig": 1,
        }
        headers = {"User-Agent": self.user_agent}

        for attempt in range(1, self.max_attempts + 1):
            try:
                if self.client is not None:
                    response = self.client.get(
                        DUCKDUCKGO_API, params=params, headers=headers,
                        timeout=self.timeout,
                    )
                else:
                    response = httpx.get(
                        DUCKDUCKGO_API, params=params, headers=headers,
                        timeout=self.timeout,
                    )
                if response.status_code == 202:
                    wait = self.delay_seconds * attempt
                    logger.debug("Search throttled for %r; waiting %.1fs", query, wait)
                    time.sleep(wait)
                    continue
                response.raise_for_status()
                payload: Any = response.json()
            except Exception as exc:  # noqa: BLE001 - research must never break collection
                logger.debug("Search backend failed for %r: %s", query, exc)
                if self.delay_seconds:
                    time.sleep(self.delay_seconds * attempt)
                continue

            results = _to_evidence(payload, query, limit)
            logger.debug("DuckDuckGo returned %d item(s) for %r", len(results), query)
            if self.delay_seconds:
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
                title=payload.get("Heading") or query,
                claim=" ".join(str(abstract).split())[:600],
                source_url=abstract_url or "https://duckduckgo.com",
                source_type="reference",
            )
        )

    for topic in (payload.get("RelatedTopics") or [])[:limit]:
        text = topic.get("Text")
        url = topic.get("FirstURL")
        if not text:
            for nested in topic.get("Topics", [])[:limit]:
                if nested.get("Text"):
                    results.append(
                        Evidence(
                            title=query,
                            claim=" ".join(str(nested["Text"]).split())[:400],
                            source_url=nested.get("FirstURL") or "https://duckduckgo.com",
                            source_type="web",
                        )
                    )
            continue
        results.append(
            Evidence(
                title=query,
                claim=" ".join(str(text).split())[:400],
                source_url=url or "https://duckduckgo.com",
                source_type="web",
            )
        )
        if len(results) >= limit + 1:
            break

    return results


@dataclass
class WikipediaBackend(SearchBackend):
    """Wikipedia summary plus the official website from Wikidata."""

    timeout: float = 20.0
    language: str = "en"
    max_results: int = 3
    delay_seconds: float = 0.2
    user_agent: str = "placement-intelligence/0.1 (contact: local@example.invalid)"
    client: httpx.Client | None = None

    @property
    def _api(self) -> str:
        return f"https://{self.language}.wikipedia.org/w/api.php"

    @property
    def _summary(self) -> str:
        return f"https://{self.language}.wikipedia.org/api/rest_v1/page/summary/"

    def search(self, query: str, limit: int | None = None) -> list[Evidence]:
        limit = min(limit or self.max_results, 5)
        if not query.strip():
            return []

        payload = _get_json(
            self._api,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "srlimit": limit,
                "srprop": "snippet|timestamp",
            },
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout,
            client=self.client,
        )
        hits = (((payload or {}).get("query") or {}).get("search") or [])[:limit]
        if not hits:
            return []

        results: list[Evidence] = []
        # Fetch full summaries for the two best hits; use snippets for the rest.
        for index, hit in enumerate(hits):
            title = str(hit.get("title") or "").strip()
            if not title:
                continue
            page_url = (
                f"https://{self.language}.wikipedia.org/wiki/"
                + quote(title.replace(" ", "_"))
            )
            summary = None
            if index < 2:
                if self.delay_seconds:
                    time.sleep(self.delay_seconds)
                summary = _get_json(
                    self._summary + quote(title.replace(" ", "_"), safe=""),
                    params={"redirect": "true"},
                    headers={"User-Agent": self.user_agent},
                    timeout=self.timeout,
                    client=self.client,
                )

            extract = None
            description = None
            published_at = None
            wikibase_item = None
            if isinstance(summary, dict):
                extract = summary.get("extract")
                description = summary.get("description")
                published_at = summary.get("timestamp")
                wikibase_item = summary.get("wikibase_item")
                content_urls = (summary.get("content_urls") or {}).get("desktop") or {}
                page_url = content_urls.get("page") or page_url

            claim = " ".join(str(extract or _strip_html(str(hit.get("snippet") or ""))).split())
            if description and extract and description.lower() not in claim.lower():
                claim = f"{description}. {claim}"
            if not claim:
                continue
            results.append(
                Evidence(
                    title=title,
                    claim=claim[:800],
                    source_url=page_url,
                    source_type="reference",
                    published_at=published_at,
                    excerpt=description,
                )
            )

            if wikibase_item and index == 0:
                website = self._official_website(str(wikibase_item))
                if website:
                    results.append(
                        Evidence(
                            title=f"{title} official website",
                            claim=f"Official website: {website}",
                            source_url=website,
                            source_type="official",
                            excerpt=(
                                "Listed on Wikidata "
                                f"(https://www.wikidata.org/wiki/{wikibase_item})"
                            ),
                        )
                    )
        return results

    def _official_website(self, wikibase_item: str) -> str | None:
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        payload = _get_json(
            WIKIDATA_API,
            params={
                "action": "wbgetentities",
                "ids": wikibase_item,
                "props": "claims",
                "format": "json",
            },
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout,
            client=self.client,
        )
        entity = (((payload or {}).get("entities") or {}).get(wikibase_item) or {})
        claims = (entity.get("claims") or {}).get("P856") or []
        for claim in claims:
            value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
        return None


@dataclass
class GdeltNewsBackend(SearchBackend):
    """Recent news articles from the free GDELT DOC 2.0 API."""

    timeout: float = 20.0
    max_results: int = 5
    timespan: str = "2y"
    min_interval_seconds: float = 0.0
    user_agent: str = "placement-intelligence/0.1 (contact: local@example.invalid)"
    client: httpx.Client | None = None

    def search(self, query: str, limit: int | None = None) -> list[Evidence]:
        limit = min(limit or self.max_results, 10)
        query = query.strip()
        if len(query) < 3:
            return []
        # News results add nothing for an "official website" query.
        if "official website" in query.lower():
            return []

        _throttle_gdelt(self.min_interval_seconds)
        payload = _get_json(
            GDELT_API,
            params={
                "query": f'"{query}"',
                "mode": "artlist",
                "maxrecords": limit,
                "format": "json",
                "sort": "datedesc",
                "timespan": self.timespan,
            },
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout,
            client=self.client,
        )
        articles = (payload or {}).get("articles") or []
        results: list[Evidence] = []
        seen: set[str] = set()
        for article in articles:
            title = " ".join(str(article.get("title") or "").split())
            url = str(article.get("url") or "").strip()
            if not title or not url or url in seen:
                continue
            language = str(article.get("language") or "").lower()
            if language and language not in ("english", "eng"):
                continue
            seen.add(url)
            domain = str(article.get("domain") or urlparse(url).hostname or "").lower()
            reputable = any(
                domain == trusted or domain.endswith("." + trusted)
                for trusted in REPUTABLE_NEWS_DOMAINS
            )
            published = _gdelt_date(article.get("seendate"))
            results.append(
                Evidence(
                    title=title,
                    claim=title[:600],
                    source_url=url,
                    source_type="news" if reputable else "news_low",
                    published_at=published,
                    excerpt=domain or None,
                )
            )

        # Reputable outlets first, then most recent; drop low-trust noise when
        # at least one reputable article exists.
        reputable_items = [item for item in results if item.source_type == "news"]
        return (reputable_items or results)[:limit]


def _gdelt_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S"):
        try:
            parsed = dt.datetime.strptime(text, fmt).replace(tzinfo=dt.timezone.utc)
            return parsed.isoformat()
        except ValueError:
            continue
    return None


@dataclass
class BraveSearchBackend(SearchBackend):
    """Optional Brave Search API backend (free tier requires a key)."""

    api_key: str
    timeout: float = 20.0
    max_results: int = 5
    client: httpx.Client | None = None

    def search(self, query: str, limit: int | None = None) -> list[Evidence]:
        if not self.api_key:
            return []
        limit = min(limit or self.max_results, 10)
        payload = _get_json(
            BRAVE_API,
            params={
                "q": query,
                "count": limit,
                "text_decorations": 0,
                "safesearch": "moderate",
            },
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key,
            },
            timeout=self.timeout,
            client=self.client,
        )
        rows = (((payload or {}).get("web") or {}).get("results") or [])[:limit]
        results: list[Evidence] = []
        for row in rows:
            url = str(row.get("url") or "").strip()
            title = " ".join(str(row.get("title") or "").split())
            description = _strip_html(str(row.get("description") or ""))
            if not url or not title:
                continue
            results.append(
                Evidence(
                    title=title,
                    claim=description or title,
                    source_url=url,
                    source_type="web",
                    published_at=row.get("page_age") or row.get("age"),
                )
            )
        return results


@dataclass
class CompositeSearchBackend(SearchBackend):
    """Query several backends and merge by trust, then source order."""

    backends: list[SearchBackend]
    max_results: int = 5

    def search(self, query: str, limit: int | None = None) -> list[Evidence]:
        limit = limit or self.max_results
        merged: list[Evidence] = []
        for backend in self.backends:
            try:
                items = backend.search(query, limit=limit)
            except Exception as exc:  # noqa: BLE001 - one backend must not stop the rest
                logger.debug("%s failed for %r: %s", type(backend).__name__, query, exc)
                continue
            merged.extend(items)

        # Trust first, then source order. De-duplication happens after sorting so
        # an official URL beats the same URL reported by a weaker backend.
        merged.sort(key=lambda item: _TRUST_RANK.get(item.source_type, 9))
        unique: list[Evidence] = []
        seen_urls: set[str] = set()
        seen_claims: set[str] = set()
        for item in merged:
            url_key = (item.source_url or "").rstrip("/").lower()
            claim_key = " ".join((item.claim or "").lower().split())[:100]
            if url_key and url_key in seen_urls:
                continue
            if claim_key and claim_key in seen_claims:
                continue
            if url_key:
                seen_urls.add(url_key)
            if claim_key:
                seen_claims.add(claim_key)
            unique.append(item)
        return unique[:limit]


@dataclass
class CachedSearchBackend:
    """Disk cache in front of any backend (7 days by default)."""

    backend: SearchBackend
    cache: FileCache
    label: str = "search"

    def search(self, query: str, limit: int = 5) -> list[Evidence]:
        key = f"{self.label}:{query.strip().lower()}:{limit}"
        cached = self.cache.get(key)
        if isinstance(cached, list):
            try:
                return [Evidence(**item) for item in cached]
            except TypeError:
                pass

        results = self.backend.search(query, limit=limit)
        self.cache.set(key, [item.__dict__ for item in results])
        return results


def search_queries(
    queries: Iterable[str],
    *,
    backend: SearchBackend | None = None,
    limit: int = 5,
    max_results: int = 8,
    settings: Settings | None = None,
) -> list[Evidence]:
    """Run several queries and merge unique, cited evidence."""
    backend = backend or default_search_backend(settings)
    results: list[Evidence] = []
    seen: set[str] = set()
    for query in queries:
        if len(results) >= max_results:
            break
        for item in backend.search(query, limit=limit):
            key = (item.source_url or item.claim[:100]).rstrip("/").lower()
            if key in seen:
                continue
            seen.add(key)
            results.append(item)
    return results[:max_results]


def default_search_backend(settings: Settings | None = None) -> SearchBackend:
    """Build the configured backend, wrapped in the file cache."""
    settings = settings or get_settings()
    choice = (settings.search_backend or "auto").strip().lower()

    wikipedia = WikipediaBackend(
        timeout=settings.search_timeout_seconds,
        max_results=settings.search_max_results,
        user_agent=settings.search_user_agent,
    )
    gdelt = GdeltNewsBackend(
        timeout=settings.search_timeout_seconds,
        max_results=settings.search_max_results,
        timespan=settings.search_gdelt_timespan,
        min_interval_seconds=5.2,
        user_agent=settings.search_user_agent,
    )
    duckduckgo = DuckDuckGoBackend(
        timeout=settings.search_timeout_seconds,
        max_results=settings.search_max_results,
        delay_seconds=0.0,
        user_agent=settings.search_user_agent,
    )

    if choice in ("auto", "composite"):
        backends: list[SearchBackend] = [wikipedia, gdelt]
        if settings.brave_search_api_key:
            backends.insert(
                1,
                BraveSearchBackend(
                    api_key=settings.brave_search_api_key,
                    timeout=settings.search_timeout_seconds,
                ),
            )
        backends.append(duckduckgo)
        backend: SearchBackend = CompositeSearchBackend(
            backends=backends, max_results=settings.search_max_results
        )
    elif choice == "wikipedia":
        backend = wikipedia
    elif choice == "gdelt":
        backend = gdelt
    elif choice == "brave":
        backend = BraveSearchBackend(
            api_key=settings.brave_search_api_key,
            timeout=settings.search_timeout_seconds,
        )
    elif choice == "duckduckgo":
        backend = duckduckgo
    else:
        logger.warning("Unknown SEARCH_BACKEND %r; using auto.", choice)
        backend = CompositeSearchBackend(
            backends=[wikipedia, gdelt, duckduckgo],
            max_results=settings.search_max_results,
        )

    cache = FileCache(settings.search_cache_dir, settings.search_cache_ttl_seconds)
    return CachedSearchBackend(backend=backend, cache=cache, label=choice)
