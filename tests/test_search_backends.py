"""Tests for the composite search backend and its cache (week 1)."""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.services.cache import FileCache
from app.services.research import Evidence
from app.services.search import (
    CachedSearchBackend,
    CompositeSearchBackend,
    DuckDuckGoBackend,
    GdeltNewsBackend,
    WikipediaBackend,
    default_search_backend,
    search_queries,
)


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_wikipedia_backend_returns_summary_and_official_site():
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "en.wikipedia.org" and request.url.path == "/w/api.php":
            return httpx.Response(
                200,
                json={
                    "query": {
                        "search": [
                            {
                                "title": "Zensar Technologies",
                                "snippet": "<span>Indian IT services company</span>",
                                "timestamp": "2026-01-01T00:00:00Z",
                            }
                        ]
                    }
                },
            )
        if host == "en.wikipedia.org" and request.url.path.startswith(
            "/api/rest_v1/page/summary/"
        ):
            return httpx.Response(
                200,
                json={
                    "extract": "Zensar Technologies is an Indian IT services company.",
                    "description": "Indian technology company",
                    "timestamp": "2026-01-02T00:00:00Z",
                    "wikibase_item": "Q123",
                    "content_urls": {
                        "desktop": {
                            "page": "https://en.wikipedia.org/wiki/Zensar_Technologies"
                        }
                    },
                },
            )
        if host == "www.wikidata.org":
            return httpx.Response(
                200,
                json={
                    "entities": {
                        "Q123": {
                            "claims": {
                                "P856": [
                                    {
                                        "mainsnak": {
                                            "datavalue": {
                                                "value": "https://www.zensar.com/"
                                            }
                                        }
                                    }
                                ]
                            }
                        }
                    }
                },
            )
        return httpx.Response(404)

    backend = WikipediaBackend(
        client=client_for(handler), delay_seconds=0, max_results=3
    )
    results = backend.search("Zensar", limit=2)

    assert results[0].source_type == "reference"
    assert results[0].source_url.startswith("https://en.wikipedia.org/wiki/")
    assert "Indian IT services" in results[0].claim
    official = [item for item in results if item.source_type == "official"]
    assert official
    assert official[0].source_url == "https://www.zensar.com/"
    assert "Wikidata" in (official[0].excerpt or "")


def test_wikipedia_backend_degrades_on_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    backend = WikipediaBackend(client=client_for(handler), delay_seconds=0)
    assert backend.search("Zensar") == []


def test_gdelt_keeps_reputable_outlets_when_present():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.gdeltproject.org"
        return httpx.Response(
            200,
            json={
                "articles": [
                    {
                        "title": "Zensar wins a large deal",
                        "url": "https://www.reuters.com/zensar-deal",
                        "domain": "reuters.com",
                        "language": "English",
                        "seendate": "20260901T120000Z",
                    },
                    {
                        "title": "Zensar clickbait",
                        "url": "https://example.com/zensar",
                        "domain": "example.com",
                        "language": "English",
                        "seendate": "20260902T120000Z",
                    },
                ]
            },
        )

    backend = GdeltNewsBackend(client=client_for(handler))
    results = backend.search("Zensar", limit=5)

    assert len(results) == 1
    assert results[0].source_type == "news"
    assert results[0].source_url.endswith("/zensar-deal")
    assert results[0].published_at == "2026-09-01T12:00:00+00:00"


def test_gdelt_labels_low_trust_when_no_reputable_outlet():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "articles": [
                    {
                        "title": "Some blog post",
                        "url": "https://example.com/post",
                        "domain": "example.com",
                        "language": "English",
                        "seendate": "20260901T120000Z",
                    }
                ]
            },
        )

    backend = GdeltNewsBackend(client=client_for(handler))
    results = backend.search("Zensar", limit=5)
    assert results[0].source_type == "news_low"


class FakeBackend:
    def __init__(self, items):
        self.items = items
        self.calls = 0

    def search(self, query: str, limit: int = 5):
        self.calls += 1
        return list(self.items[:limit])


def test_composite_orders_by_trust_and_dedupes_urls():
    shared = "https://example.com/zensar"
    web = FakeBackend(
        [
            Evidence(claim="web copy", source_url=shared, source_type="web"),
            Evidence(claim="blog", source_url="https://blog.example.com/x", source_type="web"),
        ]
    )
    official = FakeBackend(
        [Evidence(claim="official", source_url=shared, source_type="official")]
    )
    news = FakeBackend(
        [Evidence(claim="news", source_url="https://reuters.com/x", source_type="news")]
    )

    backend = CompositeSearchBackend(backends=[web, news, official])
    results = backend.search("Zensar", limit=5)

    assert [item.source_type for item in results] == ["official", "news", "web"]
    assert results[0].claim == "official"
    assert len([item for item in results if item.source_url == shared]) == 1


def test_cached_backend_avoids_repeat_calls(tmp_path):
    fake = FakeBackend([Evidence(claim="cached", source_url="https://x.test/a")])
    cache = FileCache(tmp_path, ttl_seconds=3600)
    backend = CachedSearchBackend(backend=fake, cache=cache)

    first = backend.search("zensar")
    second = backend.search("zensar")

    assert first[0].claim == second[0].claim
    assert fake.calls == 1


def test_disabled_cache_is_a_miss(tmp_path):
    fake = FakeBackend([Evidence(claim="x", source_url="https://x.test/a")])
    backend = CachedSearchBackend(backend=fake, cache=FileCache(tmp_path, ttl_seconds=0))
    backend.search("zensar")
    backend.search("zensar")
    assert fake.calls == 2


def test_search_queries_dedupes_by_url():
    backend = FakeBackend(
        [
            Evidence(claim="one", source_url="https://x.test/a"),
            Evidence(claim="two", source_url="https://x.test/b"),
        ]
    )
    results = search_queries(["a", "b"], backend=backend, limit=5, max_results=5)
    assert [item.claim for item in results] == ["one", "two"]


def test_default_backend_honours_explicit_choice(tmp_path):
    settings = Settings(
        database_url="sqlite://",
        search_backend="wikipedia",
        search_cache_dir=str(tmp_path),
        search_cache_ttl_hours=0,
    )
    backend = default_search_backend(settings)
    assert isinstance(backend, CachedSearchBackend)
    assert isinstance(backend.backend, WikipediaBackend)


def test_unknown_backend_falls_back_to_composite(tmp_path):
    settings = Settings(
        database_url="sqlite://",
        search_backend="nonsense",
        search_cache_dir=str(tmp_path),
        search_cache_ttl_hours=0,
    )
    backend = default_search_backend(settings)
    assert isinstance(backend.backend, CompositeSearchBackend)


def test_duckduckgo_fallback_still_works():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "Heading": "Zensar",
                "AbstractText": "Zensar is an IT company.",
                "AbstractURL": "https://en.wikipedia.org/wiki/Zensar",
            },
        )

    backend = DuckDuckGoBackend(
        client=client_for(handler), delay_seconds=0, max_attempts=1
    )
    results = backend.search("Zensar")
    assert results[0].source_type == "reference"
    assert results[0].title == "Zensar"
