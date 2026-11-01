"""Automatic polling writes a correct systemd timer and the web app controls it."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.services.auto_poll import AutoPoll, AutoPollError, service_unit, timer_unit

PROJECT = Path("/home/student/6th Sem/copilot 100%")


class FakeSystemctl:
    def __init__(self, failing: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.failing = failing

    def __call__(self, arguments):
        self.calls.append(list(arguments))
        if arguments[0] == self.failing:
            return subprocess.CompletedProcess(arguments, 1, "", "Unit not found.")
        if arguments[0] == "show":
            stdout = "ActiveState=active\nNextElapseUSecRealtime=Sun 2026-11-01 16:30:00 IST\n"
            return subprocess.CompletedProcess(arguments, 0, stdout, "")
        return subprocess.CompletedProcess(arguments, 0, "", "")


@pytest.fixture()
def systemctl() -> FakeSystemctl:
    return FakeSystemctl()


@pytest.fixture()
def auto_poll(tmp_path, systemctl) -> AutoPoll:
    return AutoPoll(unit_dir=tmp_path, project_dir=PROJECT, python="/venv/bin/python", run=systemctl)


def test_service_runs_the_poll_then_delivers_alerts_from_the_project():
    unit = service_unit(PROJECT, "/venv/bin/python")
    assert "WorkingDirectory=/home/student/6th Sem/copilot 100%%\n" in unit
    assert '"/venv/bin/python" -m app.main poll' in unit
    assert unit.index("app.main poll") < unit.index("app.main deliver")


def test_timer_repeats_at_the_chosen_interval():
    assert "OnUnitActiveSec=15min" in timer_unit(15)


def test_turning_on_installs_and_starts_the_timer(auto_poll, systemctl, tmp_path):
    auto_poll.turn_on(15)
    assert (tmp_path / "placement-copilot-poll.timer").exists()
    assert ["enable", "placement-copilot-poll.timer"] in systemctl.calls
    assert ["restart", "placement-copilot-poll.timer"] in systemctl.calls


def test_status_reports_the_interval_and_next_run(auto_poll):
    auto_poll.turn_on(30)
    status = auto_poll.status()
    assert status.is_on and status.interval_minutes == 30
    assert status.next_run.strftime("%H:%M") == "16:30"


def test_status_is_off_before_anything_is_installed(auto_poll):
    assert auto_poll.status().is_on is False


def test_turning_off_removes_the_timer(auto_poll, systemctl, tmp_path):
    auto_poll.turn_on(15)
    auto_poll.turn_off()
    assert not (tmp_path / "placement-copilot-poll.timer").exists()
    assert ["disable", "--now", "placement-copilot-poll.timer"] in systemctl.calls


def test_unsupported_interval_is_refused(auto_poll):
    with pytest.raises(AutoPollError):
        auto_poll.turn_on(1)


def test_systemd_failure_is_explained(tmp_path):
    failing = AutoPoll(unit_dir=tmp_path, project_dir=PROJECT, run=FakeSystemctl(failing="enable"))
    with pytest.raises(AutoPollError, match="Unit not found"):
        failing.turn_on(15)


# --- web app -------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, auto_poll):
    from agent_helpers import make_settings
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from test_web import FakeJobRunner

    from app.models import Base
    from app.web.server import create_app

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    settings = make_settings(superset_storage_state=str(tmp_path / "state.json"))
    app = create_app(sessionmaker(bind=engine, expire_on_commit=False), settings, FakeJobRunner(), auto_poll)
    return TestClient(app), sessionmaker(bind=engine, expire_on_commit=False)


def test_profile_offers_to_turn_automatic_polling_on(client):
    web, _ = client
    assert "Automatic polling is off" in web.get("/profile").text


def test_turning_on_from_the_profile_shows_the_schedule(client):
    web, _ = client
    page = web.post("/auto-poll", data={"interval": "30"}).text
    assert "Checks Superset every 30 minutes" in page and "next at 16:30" in page


def test_turning_off_from_the_profile(client):
    web, _ = client
    web.post("/auto-poll", data={"interval": "15"})
    assert "Automatic polling is off" in web.post("/auto-poll/off").text


def test_open_pages_hear_about_newer_polls(client):
    import datetime as dt

    from app.models import PollRun

    web, factory = client
    with factory() as session:
        session.add(PollRun(status="success", items_new=2, items_changed=1,
                            finished_at=dt.datetime.now(dt.UTC)))
        session.commit()
    assert "2 new, 1 changed" in web.get("/poll/updates?since=0").text
    assert "New from Superset" not in web.get("/poll/updates?since=1").text


def test_a_poll_still_running_is_not_announced(client):
    from app.models import PollRun

    web, factory = client
    with factory() as session:
        session.add(PollRun(status="running", items_new=3))
        session.commit()
    assert "New from Superset" not in web.get("/poll/updates?since=0").text
