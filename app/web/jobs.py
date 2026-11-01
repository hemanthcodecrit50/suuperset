"""Run slow CLI commands (poll, login, agent) in the background for the web app."""

from __future__ import annotations

import datetime as dt
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

LOG_TAIL_LINES = 6


@dataclass
class Job:
    name: str
    command: list[str]
    log_path: Path
    process: subprocess.Popen
    started_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))

    @property
    def state(self) -> str:
        code = self.process.poll()
        if code is None:
            return "running"
        return "succeeded" if code == 0 else "failed"

    @property
    def elapsed_seconds(self) -> int:
        return int((dt.datetime.now(dt.UTC) - self.started_at).total_seconds())

    def log_tail(self) -> str:
        if not self.log_path.exists():
            return ""
        lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-LOG_TAIL_LINES:])


class JobRunner:
    """Starts each named job at most once at a time and remembers its outcome."""

    def __init__(self, log_dir: Path) -> None:
        self.log_dir = log_dir
        self.jobs: dict[str, Job] = {}

    def start(self, name: str, cli_args: list[str]) -> Job:
        existing = self.jobs.get(name)
        if existing is not None and existing.state == "running":
            return existing
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / f"{name}.log"
        command = [sys.executable, "-m", "app.main", *cli_args]
        with log_path.open("w", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                command, stdout=log_file, stderr=subprocess.STDOUT
            )
        job = Job(name=name, command=command, log_path=log_path, process=process)
        self.jobs[name] = job
        return job

    def get(self, name: str) -> Job | None:
        return self.jobs.get(name)
