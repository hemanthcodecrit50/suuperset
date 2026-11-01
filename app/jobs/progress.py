"""Poll progress written to a small file so the web app can show a live progress bar."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from app.services.events import EventLog

# Each stage owns a slice of the bar: (label, start percent, end percent)
STAGES: dict[str, tuple[str, int, int]] = {
    "connect": ("Opening Superset with your saved session", 0, 10),
    "announcements": ("Reading announcements", 10, 20),
    "jobs": ("Reading job profiles", 20, 60),
    "save": ("Saving postings and checking for changes", 60, 95),
    "done": ("Finished", 100, 100),
    "failed": ("Stopped", 100, 100),
}


@dataclass
class PollProgress:
    stage: str
    label: str
    percent: int
    detail: str
    updated_at: str


class ProgressReporter:
    """Writes the current poll stage; does nothing when no file is configured."""

    def __init__(
        self,
        path: Path | None,
        events: EventLog | None = None,
        run_id: str | None = None,
    ) -> None:
        self.path = path
        self.events = events
        self.run_id = run_id
        self.stage: str | None = None

    def record(self, name: str, summary: str, payload: dict | None = None) -> None:
        """Log a collector event for Dev mode."""
        if self.events is not None:
            self.events.record(
                "collector", name, summary, run_id=self.run_id, payload=payload
            )

    def update(
        self, stage: str, detail: str = "", done: int = 0, total: int = 0
    ) -> None:
        if stage != self.stage:
            self.stage = stage
            # Mid-stage counts go stale at once; only the final outcome is worth keeping
            is_final = stage in ("done", "failed")
            summary = (
                f"{STAGES[stage][0]} · {detail}"
                if is_final and detail
                else STAGES[stage][0]
            )
            self.record("stage", summary)
        if self.path is None:
            return
        label, start, end = STAGES[stage]
        fraction = done / total if total else 0.0
        progress = PollProgress(
            stage=stage,
            label=label,
            percent=round(start + (end - start) * min(fraction, 1.0)),
            detail=detail,
            updated_at=dt.datetime.now(dt.UTC).isoformat(),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write then rename, so the web app never reads a half-written file
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(progress)), encoding="utf-8")
        temporary.replace(self.path)


def read_progress(path: Path) -> PollProgress | None:
    """Latest progress, or None before the first poll has reported anything."""
    try:
        return PollProgress(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
