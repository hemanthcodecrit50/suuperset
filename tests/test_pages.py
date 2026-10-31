"""Tests for the safe page-fetch tool (week 1)."""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.services import pages

PUBLIC_IP = "93.184.216.34"


def make_settings(tmp_path, **overrides) -> Settings:
    values = {
        "database_url": "sqlite://",
        "search_cache_dir": str(tmp_path),
        "fetch_page_allowed_domains": "example.com,wikipedia.org",
        "fetch_page_cache_ttl_hours": 0,
        "fetch_page_max_bytes": 200_000,
        "fetch_page_max_chars": 20_000,
        "fetch_page_timeout_seconds": 5,
    }
    values.update(overrides)
    return Settings(**values)


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(pages, "_resolve_ips", lambda host: {PUBLIC_IP})


def test_fetches_allowed_page_and_extracts_text(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.url.host
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=(
                "<html><head><title>Example article</title>"
                "<script>alert('bad')</script></head>"
                "<body><article><p>Hello article body with facts.</p>"
                "<p>Second paragraph.</p></article></body></html>"
            ),
        )

    page = pages.fetch_page(
        "https://example.com/page",
        settings=make_settings(tmp_path),
        client=client_for(handler),
    )

    assert seen["host"] == "example.com"
    assert page.status == 200
    assert page.title == "Example article"
    assert "Hello article body" in page.text
    assert "alert" not in page.text
    assert page.url == "https://example.com/page"
    assert page.truncated is False


def test_disallowed_host_fails_closed(tmp_path):
    called = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["count"] += 1
        return httpx.Response(200, text="should not happen")

    with pytest.raises(pages.PageFetchError, match="allow-list"):
        pages.fetch_page(
            "https://not-allowed.example.org/page",
            settings=make_settings(tmp_path),
            client=client_for(handler),
        )
    assert called["count"] == 0


def test_per_call_allow_domains(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="Trusted page text",
        )

    page = pages.fetch_page(
        "https://company.example.org/about",
        allow_domains=["company.example.org"],
        settings=make_settings(tmp_path),
        client=client_for(handler),
    )
    assert page.text == "Trusted page text"


def test_redirect_to_disallowed_host_is_blocked(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "https://evil.example.org/trap"},
        )

    with pytest.raises(pages.PageFetchError, match="allow-list"):
        pages.fetch_page(
            "https://example.com/redirect",
            settings=make_settings(tmp_path),
            client=client_for(handler),
        )


def test_private_address_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(pages, "_resolve_ips", lambda host: {"10.0.0.5"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="internal")

    with pytest.raises(pages.PageFetchError, match="non-public"):
        pages.fetch_page(
            "https://example.com/internal",
            settings=make_settings(tmp_path),
            client=client_for(handler),
        )


def test_only_http_and_https(tmp_path):
    with pytest.raises(pages.PageFetchError, match="http"):
        pages.fetch_page("file:///etc/passwd", settings=make_settings(tmp_path))


def test_credentials_in_url_are_rejected(tmp_path):
    with pytest.raises(pages.PageFetchError, match="credentials"):
        pages.fetch_page(
            "https://user:pass@example.com/",
            settings=make_settings(tmp_path),
        )


def test_unsupported_content_type_is_rejected(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF-1.4",
        )

    with pytest.raises(pages.PageFetchError, match="content type"):
        pages.fetch_page(
            "https://example.com/file.pdf",
            settings=make_settings(tmp_path),
            client=client_for(handler),
        )


def test_size_and_character_caps(tmp_path):
    payload = "<html><body>" + ("x" * 500) + "</body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=payload,
        )

    page = pages.fetch_page(
        "https://example.com/big",
        settings=make_settings(tmp_path, fetch_page_max_bytes=50),
        client=client_for(handler),
    )
    assert page.bytes_read == 50
    assert page.truncated is True

    page = pages.fetch_page(
        "https://example.com/short",
        max_chars=10,
        settings=make_settings(tmp_path, fetch_page_max_bytes=200_000),
        client=client_for(handler),
    )
    assert page.chars == 10
    assert page.truncated is True


def test_cache_avoids_second_request(tmp_path):
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><body><p>Cached fact.</p></body></html>",
        )

    settings = make_settings(tmp_path, fetch_page_cache_ttl_hours=1)
    client = client_for(handler)
    first = pages.fetch_page("https://example.com/cached", settings=settings, client=client)
    second = pages.fetch_page("https://example.com/cached", settings=settings, client=client)

    assert first.text == second.text
    assert calls["count"] == 1


def test_host_allowed_matches_subdomains():
    assert pages.host_allowed("news.example.com", ["example.com"]) is True
    assert pages.host_allowed("example.com", ["example.com"]) is True
    assert pages.host_allowed("notexample.com", ["example.com"]) is False


def test_cache_does_not_bypass_the_allow_list(tmp_path):
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><body><p>Trusted once.</p></body></html>",
        )

    settings = make_settings(tmp_path, fetch_page_cache_ttl_hours=1)
    client = client_for(handler)
    page = pages.fetch_page(
        "https://company.example.org/about",
        allow_domains=["company.example.org"],
        settings=settings,
        client=client,
    )
    assert page.text == "Trusted once."

    with pytest.raises(pages.PageFetchError, match="allow-list"):
        pages.fetch_page(
            "https://company.example.org/about",
            settings=settings,
            client=client,
        )
    assert calls["count"] == 1
