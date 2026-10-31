"""Fetch a cited page as plain text for verification.

Safety rules:
- only ``http``/``https`` URLs;
- the host must be on an allow-list (configured domains plus per-call extras);
- private, loopback, link-local and reserved addresses are rejected (SSRF);
- every redirect target is re-validated before it is followed;
- responses are size-capped and text-extracted, never rendered or executed.

The agent uses this after ``web_search`` to check that a claim actually appears
on the page it cites.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import logging
import socket
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import httpx

from app.config import Settings, get_settings
from app.services.cache import FileCache

logger = logging.getLogger(__name__)

_ALLOWED_CONTENT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
)
_MAX_REDIRECTS = 4


class PageFetchError(RuntimeError):
    """Raised when a page cannot be fetched safely."""


@dataclass
class FetchedPage:
    url: str
    final_url: str
    status: int
    title: str | None
    text: str
    content_type: str | None
    bytes_read: int
    chars: int
    truncated: bool
    fetched_at: str

    def to_dict(self) -> dict:
        return asdict(self)


def _cache(settings: Settings) -> FileCache:
    return FileCache(
        Path(settings.search_cache_dir) / "pages",
        ttl_seconds=max(0, settings.fetch_page_cache_ttl_hours) * 3600,
    )


def normalize_domain(value: str) -> str:
    """Accept ``example.com``, ``www.example.com`` or a URL and return a host."""
    text = str(value).strip().lower()
    if "://" in text:
        text = urlparse(text).hostname or ""
    text = text.split("/")[0].split(":")[0].strip(".")
    if text.startswith("www."):
        text = text[4:]
    return text


def host_allowed(host: str, domains: Iterable[str]) -> bool:
    host = normalize_domain(host)
    if not host:
        return False
    for domain in domains:
        domain = normalize_domain(domain)
        if domain and (host == domain or host.endswith("." + domain)):
            return True
    return False


def _resolve_ips(host: str) -> set[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise PageFetchError(f"Could not resolve host {host!r}: {exc}") from exc
    return {info[4][0] for info in infos if info[4]}


def _assert_public_host(host: str) -> None:
    """Reject loopback/private/link-local/reserved targets."""
    try:
        addresses = {str(ipaddress.ip_address(host))}
    except ValueError:
        addresses = _resolve_ips(host)

    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise PageFetchError(f"Invalid resolved address {address!r}") from None
        if not ip.is_global:
            raise PageFetchError(
                f"Refusing to fetch a non-public address for host {host!r}."
            )


def _validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise PageFetchError("Only http and https URLs can be fetched.")
    if not parsed.hostname:
        raise PageFetchError("The URL has no host.")
    if parsed.username or parsed.password:
        raise PageFetchError("URLs containing credentials are not allowed.")
    return url


def _extract(html: str, content_type: str | None) -> tuple[str | None, str]:
    """Return ``(title, text)`` without executing anything."""
    if content_type and content_type.startswith("text/plain"):
        return None, html

    title: str | None = None
    text = ""
    try:
        import trafilatura

        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        ) or ""
    except Exception as exc:  # noqa: BLE001 - extraction is best effort
        logger.debug("trafilatura extraction failed: %s", exc)

    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        if soup.title and soup.title.string:
            title = " ".join(soup.title.string.split())[:300]
        if not text:
            for tag in soup(
                ["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]
            ):
                tag.decompose()
            text = soup.get_text("\n")
    except Exception as exc:  # noqa: BLE001 - fall back to a crude strip
        logger.debug("BeautifulSoup extraction failed: %s", exc)
        if not text:
            import re

            text = re.sub(r"<[^>]+>", " ", html)

    lines = [" ".join(line.split()) for line in text.splitlines()]
    return title, "\n".join(line for line in lines if line)


def fetch_page(
    url: str,
    *,
    allow_domains: Iterable[str] = (),
    max_chars: int | None = None,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
    use_cache: bool = True,
) -> FetchedPage:
    """Fetch one allow-listed URL and return extracted text.

    ``allow_domains`` adds domains for this call only, e.g. the official site
    discovered in search evidence. Everything else fails closed.
    """
    settings = settings or get_settings()
    max_chars = max_chars or settings.fetch_page_max_chars
    domains = tuple(settings.fetch_page_domains) + tuple(normalize_domain(d) for d in allow_domains)

    url = _validate_url(url)
    host = (urlparse(url).hostname or "").lower()
    if not host_allowed(host, domains):
        raise PageFetchError(
            f"Host {host!r} is not on the fetch allow-list. "
            "Pass it explicitly with allow_domains if it is trusted."
        )

    cache = _cache(settings)
    cache_key = f"page:{url}:{max_chars}"
    if use_cache:
        cached = cache.get(cache_key)
        if isinstance(cached, dict):
            try:
                return FetchedPage(**cached)
            except TypeError:
                pass

    timeout = settings.fetch_page_timeout_seconds
    headers = {
        "User-Agent": settings.search_user_agent,
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
    }

    owns_client = client is None
    active = client or httpx.Client(timeout=timeout, headers=headers)
    current = url
    try:
        body = b""
        status = 0
        final_url = current
        content_type: str | None = None
        encoding = "utf-8"
        truncated = False

        for _ in range(_MAX_REDIRECTS + 1):
            parsed = urlparse(current)
            host = (parsed.hostname or "").lower()
            if not host_allowed(host, domains):
                raise PageFetchError(
                    f"Host {host!r} is not on the fetch allow-list. "
                    "Pass it explicitly with allow_domains if it is trusted."
                )
            _assert_public_host(host)

            with active.stream(
                "GET", current, headers=headers, follow_redirects=False
            ) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise PageFetchError("Redirect response had no Location header.")
                    current = urljoin(current, location)
                    _validate_url(current)
                    continue

                status = response.status_code
                final_url = str(response.url)
                content_type = (
                    (response.headers.get("content-type") or "")
                    .split(";")[0]
                    .strip()
                    .lower()
                )
                if status >= 400:
                    raise PageFetchError(f"HTTP {status} while fetching {current}.")
                if content_type and not any(
                    content_type.startswith(allowed)
                    for allowed in _ALLOWED_CONTENT_TYPES
                ):
                    raise PageFetchError(
                        f"Unsupported content type {content_type!r}; "
                        "only text pages are fetched."
                    )

                encoding = response.encoding or "utf-8"
                buffer = bytearray()
                for chunk in response.iter_bytes():
                    remaining = settings.fetch_page_max_bytes - len(buffer)
                    if remaining <= 0:
                        truncated = True
                        break
                    if len(chunk) > remaining:
                        buffer.extend(chunk[:remaining])
                        truncated = True
                        break
                    buffer.extend(chunk)
                body = bytes(buffer)
            break
        else:
            raise PageFetchError("Too many redirects.")

        try:
            html = body.decode(encoding, errors="replace")
        except LookupError:
            html = body.decode("utf-8", errors="replace")

        title, text = _extract(html, content_type)
        if len(text) > max_chars:
            text = text[:max_chars]
            truncated = True

        page = FetchedPage(
            url=url,
            final_url=final_url,
            status=status,
            title=title,
            text=text,
            content_type=content_type or None,
            bytes_read=len(body),
            chars=len(text),
            truncated=truncated,
            fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        )
        if use_cache and text:
            cache.set(cache_key, page.to_dict())
        return page
    except PageFetchError:
        raise
    except httpx.HTTPError as exc:
        raise PageFetchError(f"Network error fetching {current}: {exc}") from exc
    finally:
        if owns_client:
            active.close()
