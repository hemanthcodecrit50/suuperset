"""Append-only event log that lets Dev mode show what the agent and collector did."""

from __future__ import annotations

import datetime as dt
import fcntl
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SOURCES = ("collector", "agent", "ai", "tool")
# Trim the file back to the newest events once it grows past this size
MAX_LOG_BYTES = 4_000_000
KEEP_EVENTS_AFTER_TRIM = 5_000


@dataclass
class Event:
    at: str
    source: str
    name: str
    summary: str
    run_id: str | None = None
    duration_ms: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def time_label(self) -> str:
        return dt.datetime.fromisoformat(self.at).astimezone().strftime("%H:%M:%S")


class EventLog:
    """Writes events as JSON lines; safe to share between the web app, poll and agent."""

    def __init__(self, path: Path | None) -> None:
        self.path = path

    def record(
        self,
        source: str,
        name: str,
        summary: str,
        *,
        run_id: str | None = None,
        duration_ms: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self.path is None:
            return
        event = Event(
            at=dt.datetime.now(dt.UTC).isoformat(),
            source=source,
            name=name,
            summary=summary,
            run_id=run_id,
            duration_ms=duration_ms,
            payload=payload or {},
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.write(json.dumps(asdict(event), default=str) + "\n")
        if self.path.stat().st_size > MAX_LOG_BYTES:
            self._trim()

    def _trim(self) -> None:
        with self.path.open("r+", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            newest = handle.readlines()[-KEEP_EVENTS_AFTER_TRIM:]
            handle.seek(0)
            handle.truncate()
            handle.writelines(newest)


def read_events(
    path: Path,
    *,
    limit: int = 400,
    run_id: str | None = None,
    source: str | None = None,
) -> list[Event]:
    """Newest events first, optionally only one run or one source."""
    if not path.exists():
        return []
    events: list[Event] = []
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        event = _parse(line)
        if event is None:
            continue
        if run_id is not None and event.run_id != run_id:
            continue
        if source is not None and event.source != source:
            continue
        events.append(event)
        if len(events) >= limit:
            break
    return events


def _parse(line: str) -> Event | None:
    try:
        return Event(**json.loads(line))
    except (json.JSONDecodeError, TypeError):
        return None
