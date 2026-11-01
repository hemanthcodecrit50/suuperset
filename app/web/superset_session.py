"""Status of the saved Superset browser session; no password is ever stored."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

from app.models import PollRun

# Matches the SessionExpiredError messages raised by the collector
SESSION_EXPIRED_MARKERS = ("session appears expired", "app shell to load")


@dataclass(frozen=True)
class SessionStatus:
    state: str  # connected | expired | missing
    saved_at: dt.datetime | None
    message: str


def session_status(state_path: Path, latest_run: PollRun | None) -> SessionStatus:
    """Read the session file and the last poll to say whether Superset is connected."""
    if not state_path.exists():
        return SessionStatus("missing", None, "Not connected. Log in to Superset once.")
    saved_at = dt.datetime.fromtimestamp(state_path.stat().st_mtime, dt.UTC)
    if _last_poll_hit_expiry(latest_run) or _all_cookies_expired(state_path):
        return SessionStatus(
            "expired", saved_at, "Session expired. Reconnect to Superset."
        )
    return SessionStatus(
        "connected", saved_at, "Connected with a saved browser session."
    )


def disconnect(state_path: Path) -> None:
    """Delete the saved session so nothing can reach Superset until the next login."""
    state_path.unlink(missing_ok=True)


def _last_poll_hit_expiry(latest_run: PollRun | None) -> bool:
    if latest_run is None or latest_run.status != "failed" or not latest_run.error:
        return False
    error = latest_run.error.lower()
    return any(marker in error for marker in SESSION_EXPIRED_MARKERS)


def _all_cookies_expired(state_path: Path) -> bool:
    try:
        cookies = json.loads(state_path.read_text(encoding="utf-8")).get("cookies", [])
    except (OSError, json.JSONDecodeError):
        return False
    expiring = [
        cookie["expires"] for cookie in cookies if cookie.get("expires", -1) > 0
    ]
    if not expiring:
        return False
    return max(expiring) < dt.datetime.now(dt.UTC).timestamp()
