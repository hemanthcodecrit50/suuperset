"""Daily cap and pacing for model requests, shared by every process on this machine."""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import time
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

# Gemini's free-tier daily quota resets at midnight Pacific time
QUOTA_TIMEZONE = ZoneInfo("America/Los_Angeles")


@dataclass(frozen=True)
class Usage:
    used: int
    limit: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


class LlmBudget:
    """Counts requests in a locked file so the web app, poll and agent share one budget."""

    def __init__(
        self, path: Path, daily_limit: int, min_interval_seconds: float
    ) -> None:
        self.path = path
        self.daily_limit = daily_limit
        self.min_interval_seconds = min_interval_seconds

    def acquire(self) -> bool:
        """Reserve one request; wait out the pacing gap. False once today's cap is used."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            record = self._read(handle)
            if record["count"] >= self.daily_limit:
                return False
            # Sleeping while holding the lock makes other processes queue behind us
            wait = record["last_call"] + self.min_interval_seconds - time.time()
            if wait > 0:
                time.sleep(wait)
            record["count"] += 1
            record["last_call"] = time.time()
            self._write(handle, record)
            return True

    def usage(self) -> Usage:
        if not self.path.exists():
            return Usage(0, self.daily_limit)
        with self.path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_SH)
            return Usage(self._read(handle)["count"], self.daily_limit)

    def _read(self, handle) -> dict:
        handle.seek(0)
        text = handle.read()
        record = json.loads(text) if text.strip() else {}
        if record.get("day") != _quota_day():
            return {
                "day": _quota_day(),
                "count": 0,
                "last_call": record.get("last_call", 0.0),
            }
        return record

    def _write(self, handle, record: dict) -> None:
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(record))
        handle.flush()


def _quota_day() -> str:
    return dt.datetime.now(QUOTA_TIMEZONE).date().isoformat()
