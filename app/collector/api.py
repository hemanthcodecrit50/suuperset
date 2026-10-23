"""Read-only JSON API client for the Superset student app.

Discovered by inspecting the portal's own network traffic (see
docs/portal_structure.md). Endpoints under ``/tnpsuite-core/`` return clean
structured JSON, which is far more reliable than scraping rendered DOM.

Authentication
--------------
No token is ever hardcoded or stored in the repo. The ``authorization:
Custom <key>`` header is derived at runtime from the authenticated browser
session in one of two ways:

1. Captured from the page's own outgoing requests (preferred).
2. Derived from the ``_sc`` cookie that ``scripts/login.py`` already saved.

Only the signed-in student's own data is read, using the same read-only
endpoints the app itself calls. Polling stays gentle (default every 30 min).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

API_PREFIX = "/tnpsuite-core"

# Header names copied from the app's own API traffic.
HEADER_NAMES = (
    "authorization",
    "x-superset-tenant-id",
    "x-superset-tenant-type",
    "x-requester-client",
)


class ApiError(RuntimeError):
    """Raised when the API returns an unexpected status or response."""


@dataclass
class SupersetApi:
    """Minimal async client bound to an authenticated Playwright context."""

    context: Any
    base_url: str
    headers: dict[str, str] = field(default_factory=dict)
    user_uuid: str | None = None
    max_attempts: int = 3
    retry_delay: float = 3.0

    # -- construction ----------------------------------------------------
    @classmethod
    async def from_context(
        cls, context: Any, settings: Settings | None = None
    ) -> "SupersetApi":
        """Build a client, capturing auth headers from a loaded page."""
        settings = settings or get_settings()
        base = settings.superset_base_url.rstrip("/")
        api = cls(context=context, base_url=base)
        await api.discover_auth()
        await api.resolve_user()
        return api

    async def discover_auth(self) -> None:
        """Capture auth headers from live traffic, or derive from the _sc cookie."""
        captured: dict[str, str] = {}

        def on_request(request: Any) -> None:
            if API_PREFIX in request.url and not captured:
                for name in HEADER_NAMES:
                    value = request.headers.get(name)
                    if value:
                        captured[name] = value

        page = await self.context.new_page()
        page.on("request", on_request)
        try:
            await page.goto(
                f"{self.base_url}/students", wait_until="domcontentloaded"
            )
            await page.wait_for_timeout(4000)
        finally:
            await page.close()

        if "authorization" in captured:
            logger.info("Captured API auth headers from live page traffic.")
        else:
            # Fall back to the _sc cookie saved by scripts/login.py.
            for cookie in await self.context.cookies():
                if cookie["name"] == "_sc":
                    try:
                        key = json.loads(cookie["value"]).get("key")
                    except Exception:  # noqa: BLE001 - malformed cookie
                        key = None
                    if key:
                        captured["authorization"] = f"Custom {key}"
                        logger.info("Derived API auth from the _sc cookie.")
                        break

        if "authorization" not in captured:
            raise ApiError(
                "Could not obtain API credentials. Re-run scripts/login.py."
            )

        captured.setdefault("x-requester-client", "webapp")
        captured.setdefault("x-superset-tenant-type", "STUDENT")
        captured.setdefault("accept", "application/json, text/plain, */*")
        self.headers = captured

    async def resolve_user(self) -> None:
        """Look up the signed-in student's UUID from the session endpoint."""
        data = await self._get(f"{API_PREFIX}/session")
        self.user_uuid = data.get("userUUID") or data.get("userId")
        if not self.user_uuid:
            raise ApiError("Session response did not contain a user UUID.")

    # -- transport ------------------------------------------------------
    async def _get(self, path: str, params: dict | None = None) -> Any:
        """GET with retries.

        The portal intermittently answers 5xx (e.g. under load), which is
        usually transient, so short retries avoid discarding a whole cycle.
        """
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())

        last_status: int | None = None
        for attempt in range(1, self.max_attempts + 1):
            response = await self.context.request.get(url, headers=self.headers)
            last_status = response.status
            if response.status == 200:
                return await response.json()
            if response.status < 500:
                raise ApiError(f"GET {path} -> {response.status}")
            if attempt < self.max_attempts:
                delay = self.retry_delay * attempt
                logger.warning(
                    "GET %s -> %d; retrying in %.1fs (attempt %d/%d)",
                    path, response.status, delay, attempt, self.max_attempts,
                )
                await asyncio.sleep(delay)

        raise ApiError(f"GET {path} -> {last_status} (after {self.max_attempts} attempts)")

    async def _get_optional(self, path: str, params: dict | None = None) -> Any | None:
        try:
            return await self._get(path, params=params)
        except ApiError as exc:
            logger.debug("Optional fetch failed for %s: %s", path, exc)
            return None

    # -- endpoints ------------------------------------------------------
    async def job_profiles(self) -> list[dict]:
        """All job profiles visible to the student (no pagination needed)."""
        if not self.user_uuid:
            raise ApiError("user_uuid not resolved")
        data = await self._get(f"{API_PREFIX}/students/{self.user_uuid}/job_profiles")
        return data if isinstance(data, list) else []

    async def job_profile_detail(self, job_profile_id: str) -> dict | None:
        """Full detail for one job profile (JD, CTC, eligibility)."""
        if not self.user_uuid:
            raise ApiError("user_uuid not resolved")
        return await self._get_optional(
            f"{API_PREFIX}/students/{self.user_uuid}/job_profiles/{job_profile_id}"
        )

    async def notifications(self, max_pages: int = 20, page_size: int = 50) -> list[dict]:
        """All web notifications (announcements), oldest pages until empty.

        ``page`` is 0-indexed and returns up to ``page_size`` items; an empty
        page marks the end.
        """
        seen: set[str] = set()
        collected: list[dict] = []
        for page_number in range(max_pages):
            items = await self._get_optional(
                f"{API_PREFIX}/user-notifications",
                params={"channel": "WEB", "page": page_number},
            )
            if not items:
                break
            fresh = [i for i in items if i.get("uuid") not in seen]
            if not fresh:
                break
            for item in fresh:
                seen.add(item.get("uuid", ""))
            collected.extend(fresh)
            if len(items) < page_size:
                break
        return collected


# -- mapping helpers ----------------------------------------------------

def epoch_ms_to_datetime(value: Any) -> dt.datetime | None:
    """Convert an epoch-milliseconds timestamp to an aware datetime."""
    if value in (None, "", 0):
        return None
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return None
    if millis <= 0:
        return None
    return dt.datetime.fromtimestamp(millis / 1000, tz=dt.timezone.utc)


_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(value: str | None) -> str:
    """Flatten the small amount of HTML the API returns into plain text."""
    if not value:
        return ""
    text = re.sub(r"<(br|/p|/div|/li|/h[1-6])\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)
