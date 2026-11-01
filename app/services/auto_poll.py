"""Automatic polling: a systemd user timer that checks Superset at a fixed interval.

The timer runs even when the web app is closed, as long as the student is logged in.
"""

from __future__ import annotations

import datetime as dt
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

UNIT_NAME = "placement-copilot-poll"
INTERVAL_CHOICES = (10, 15, 30, 60)
DEFAULT_INTERVAL = 15
UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
PROJECT_DIR = Path(__file__).resolve().parents[2]
INTERVAL_PATTERN = re.compile(r"^OnUnitActiveSec=(\d+)min$", re.MULTILINE)

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


class AutoPollError(RuntimeError):
    """systemd refused a change; the message is safe to show the student."""


@dataclass(frozen=True)
class AutoPollStatus:
    is_on: bool
    interval_minutes: int | None
    next_run: dt.datetime | None
    is_available: bool = True


def _run_systemctl(arguments: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *arguments],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


class AutoPoll:
    """Installs, removes and reports on the poll timer."""

    def __init__(
        self,
        unit_dir: Path | None = None,
        project_dir: Path = PROJECT_DIR,
        python: str = sys.executable,
        run: Runner = _run_systemctl,
    ) -> None:
        self.unit_dir = unit_dir or UNIT_DIR
        self.project_dir = project_dir
        self.python = python
        self.run = run

    @property
    def timer_path(self) -> Path:
        return self.unit_dir / f"{UNIT_NAME}.timer"

    @property
    def service_path(self) -> Path:
        return self.unit_dir / f"{UNIT_NAME}.service"

    def turn_on(self, interval_minutes: int) -> None:
        if interval_minutes not in INTERVAL_CHOICES:
            raise AutoPollError(f"Choose an interval of {', '.join(map(str, INTERVAL_CHOICES))} minutes.")
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        self.service_path.write_text(service_unit(self.project_dir, self.python), encoding="utf-8")
        self.timer_path.write_text(timer_unit(interval_minutes), encoding="utf-8")
        self._systemctl("daemon-reload")
        # restart, not just start: a changed interval only applies after a restart
        self._systemctl("enable", f"{UNIT_NAME}.timer")
        self._systemctl("restart", f"{UNIT_NAME}.timer")

    def turn_off(self) -> None:
        if self.timer_path.exists():
            self._systemctl("disable", "--now", f"{UNIT_NAME}.timer")
        self.timer_path.unlink(missing_ok=True)
        self.service_path.unlink(missing_ok=True)
        self._systemctl("daemon-reload")

    def status(self) -> AutoPollStatus:
        if not self.timer_path.exists():
            return AutoPollStatus(False, None, None)
        try:
            shown = self.run(
                ["show", f"{UNIT_NAME}.timer", "-p", "ActiveState", "-p", "NextElapseUSecRealtime"]
            )
        except (OSError, subprocess.SubprocessError):
            return AutoPollStatus(False, self._interval(), None, is_available=False)
        fields = dict(
            line.split("=", 1) for line in shown.stdout.splitlines() if "=" in line
        )
        return AutoPollStatus(
            is_on=fields.get("ActiveState") == "active",
            interval_minutes=self._interval(),
            next_run=_parse_systemd_time(fields.get("NextElapseUSecRealtime", "")),
        )

    def _interval(self) -> int | None:
        match = INTERVAL_PATTERN.search(self.timer_path.read_text(encoding="utf-8"))
        return int(match.group(1)) if match else None

    def _systemctl(self, *arguments: str) -> None:
        try:
            result = self.run(list(arguments))
        except (OSError, subprocess.SubprocessError) as exc:
            raise AutoPollError("systemd is not available on this computer.") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise AutoPollError(
                f"systemd could not {arguments[0]} the timer: {detail[-1] if detail else 'unknown error'}"
            )


def service_unit(project_dir: Path, python: str) -> str:
    """One poll, then any queued Telegram or email alerts are sent straight away."""
    directory = _escape(str(project_dir))
    executable = _escape(python)
    return (
        "[Unit]\n"
        "Description=Placement Copilot: check Superset for new and changed postings\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"WorkingDirectory={directory}\n"
        f'ExecStart="{executable}" -m app.main poll\n'
        f'ExecStart="{executable}" -m app.main deliver\n'
        "TimeoutStartSec=15min\n"
    )


def timer_unit(interval_minutes: int) -> str:
    return (
        "[Unit]\n"
        f"Description=Check Superset every {interval_minutes} minutes\n\n"
        "[Timer]\n"
        "OnActiveSec=1min\n"
        f"OnUnitActiveSec={interval_minutes}min\n"
        "AccuracySec=30s\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def _escape(value: str) -> str:
    # systemd expands % specifiers in unit files, so a literal % is written twice
    return value.replace("%", "%%")


def _parse_systemd_time(value: str) -> dt.datetime | None:
    # systemctl prints e.g. "Sun 2026-11-01 16:16:24 IST"; the zone name is dropped
    match = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", value)
    if match is None:
        return None
    return dt.datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").astimezone()
