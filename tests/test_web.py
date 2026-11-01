"""Tests for the local web interface."""

from __future__ import annotations

import datetime as dt
import json
import time

import pytest
from agent_helpers import make_settings
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import (
    Application,
    Base,
    Opportunity,
    PollRun,
    ResearchReport,
    ResumeSuggestion,
    Source,
)
from app.web.jobs import JobRunner
from app.web.server import create_app
from app.web.superset_session import session_status

FUTURE = dt.datetime(2999, 1, 1, tzinfo=dt.UTC)


class FakeJobRunner(JobRunner):
    def __init__(self) -> None:
        super().__init__(log_dir=None)
        self.started: list[list[str]] = []

    def start(self, name, cli_args):
        self.started.append(cli_args)


@pytest.fixture()
def factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture()
def jobs() -> FakeJobRunner:
    return FakeJobRunner()


@pytest.fixture()
def client(factory, jobs, tmp_path):
    settings = make_settings(superset_storage_state=str(tmp_path / "state.json"))
    return TestClient(create_app(factory, settings, jobs))


@pytest.fixture()
def posting_id(factory) -> int:
    with factory() as session:
        source = Source(name="superset", base_url="https://app.joinsuperset.com")
        session.add(source)
        session.flush()
        record = Opportunity(
            source_id=source.id,
            external_id="job-1",
            title="Backend Intern",
            company_name="Zensar",
            deadline=FUTURE,
            first_seen_at=dt.datetime.now(dt.UTC),
            description="Build services",
            raw={},
        )
        session.add(record)
        session.commit()
        return record.id


def _add_brief(factory, posting_id: int, markdown: str) -> None:
    with factory() as session:
        session.add(
            ResearchReport(
                opportunity_id=posting_id,
                kind="agent_brief",
                content={"markdown": markdown},
                sources=[],
            )
        )
        session.commit()


def _add_suggestion(factory, posting_id: int) -> int:
    with factory() as session:
        suggestion = ResumeSuggestion(
            opportunity_id=posting_id, requirement="Python", bullet="Built a Python API"
        )
        session.add(suggestion)
        session.commit()
        return suggestion.id


def test_inbox_lists_postings(client, posting_id):
    assert "Backend Intern" in client.get("/").text


def test_inbox_search_hides_non_matching_postings(client, posting_id):
    assert "Backend Intern" not in client.get("/?q=Infosys").text


def test_briefed_view_hides_postings_without_a_brief(client, posting_id):
    assert "Backend Intern" not in client.get("/?view=briefed").text


def test_briefed_view_shows_postings_with_a_brief(client, factory, posting_id):
    _add_brief(factory, posting_id, "## Agent brief\n\nFit")
    assert "Backend Intern" in client.get("/?view=briefed").text


def test_unknown_view_falls_back_to_all(client, posting_id):
    assert "Backend Intern" in client.get("/?view=nonsense").text


def test_posting_page_renders_the_brief(client, factory, posting_id):
    _add_brief(factory, posting_id, "### Risk flags\n\n- Bond clause")
    assert "<h3>Risk flags</h3>" in client.get(f"/postings/{posting_id}").text


def test_brief_html_from_the_web_is_escaped(client, factory, posting_id):
    _add_brief(factory, posting_id, "- <script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in client.get(f"/postings/{posting_id}").text


def test_missing_posting_returns_404(client):
    assert client.get("/postings/999").status_code == 404


def test_setting_a_status_tracks_the_application(client, factory, posting_id):
    client.post(f"/postings/{posting_id}/status", data={"status": "applied"})
    with factory() as session:
        assert session.query(Application).one().status == "applied"


def test_unknown_status_is_rejected(client, posting_id):
    response = client.post(f"/postings/{posting_id}/status", data={"status": "hired"})
    assert response.status_code == 400


def test_tracked_application_appears_on_the_applications_page(client, posting_id):
    client.post(f"/postings/{posting_id}/status", data={"status": "applied"})
    assert "Backend Intern" in client.get("/applications").text


def test_run_agent_starts_the_agent_command(client, jobs, posting_id):
    client.post(f"/postings/{posting_id}/agent")
    assert jobs.started[0][:4] == ["agent", "run", str(posting_id), "--fresh"]


def test_agent_run_gets_a_run_id_for_dev_mode(client, jobs, posting_id):
    client.post(f"/postings/{posting_id}/agent")
    assert jobs.started[0][4] == "--run-id"


def test_pending_suggestion_is_listed_for_approval(client, factory, posting_id):
    _add_suggestion(factory, posting_id)
    assert "Built a Python API" in client.get("/approvals").text


def test_nav_badge_counts_pending_suggestions(client, factory, posting_id):
    _add_suggestion(factory, posting_id)
    assert '<span class="count">1</span>' in client.get("/").text


def test_approving_marks_the_suggestion_approved(client, factory, posting_id):
    suggestion_id = _add_suggestion(factory, posting_id)
    client.post(f"/approvals/{suggestion_id}/approve")
    with factory() as session:
        assert session.get(ResumeSuggestion, suggestion_id).approved is True


def test_rejecting_deletes_the_suggestion(client, factory, posting_id):
    suggestion_id = _add_suggestion(factory, posting_id)
    client.post(f"/approvals/{suggestion_id}/reject")
    with factory() as session:
        assert session.get(ResumeSuggestion, suggestion_id) is None


def test_profile_form_saves_skills_as_a_list(client, factory):
    client.post("/profile", data={"skills": "Python, SQL", "cgpa": "8.2"})
    assert "Python, SQL" in client.get("/profile").text


def test_profile_rejects_a_non_numeric_cgpa(client):
    assert client.post("/profile", data={"cgpa": "eight"}).status_code == 400


def test_adding_a_project_lists_it_in_the_portfolio(client):
    client.post("/portfolio", data={"name": "Order Service", "technologies": "Python"})
    assert "Order Service" in client.get("/profile").text


def test_connect_starts_the_login_command(client, jobs):
    client.post("/superset/connect")
    assert jobs.started == [["login"]]


def test_disconnect_deletes_the_saved_session(client, tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text("{}")
    client.post("/superset/disconnect")
    assert not state_path.exists()


def test_run_poll_starts_the_poll_command(client, jobs):
    client.post("/poll")
    assert jobs.started == [["poll"]]


def test_cross_site_post_is_blocked(client, jobs):
    response = client.post("/poll", headers={"origin": "https://evil.example"})
    assert response.status_code == 403 and jobs.started == []


def test_session_is_missing_without_a_state_file(tmp_path):
    assert session_status(tmp_path / "none.json", None).state == "missing"


def test_session_is_connected_with_a_fresh_state_file(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"cookies": [{"expires": time.time() + 3600}]}))
    assert session_status(state_path, None).state == "connected"


def test_session_is_expired_when_every_cookie_has_expired(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"cookies": [{"expires": time.time() - 3600}]}))
    assert session_status(state_path, None).state == "expired"


def test_session_is_expired_after_a_poll_hit_the_login_page(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text("{}")
    run = PollRun(
        status="failed", error="Superset session appears expired. Re-run login."
    )
    assert session_status(state_path, run).state == "expired"


class _RunningJob:
    name = "poll"
    state = "running"
    elapsed_seconds = 12
    started_at = dt.datetime(2000, 1, 1, tzinfo=dt.UTC)

    def log_tail(self) -> str:
        return ""


def test_poll_panel_shows_the_current_stage_and_detail(factory, tmp_path):
    from app.jobs.progress import ProgressReporter

    progress_path = tmp_path / "progress.json"
    ProgressReporter(progress_path).update("jobs", "120 of 237 job profiles", 120, 237)
    jobs = FakeJobRunner()
    jobs.jobs["poll"] = _RunningJob()
    settings = make_settings(poll_progress_file=str(progress_path))
    page = TestClient(create_app(factory, settings, jobs)).get("/jobs/poll").text
    assert "Reading job profiles" in page and "120 of 237 job profiles" in page


def test_poll_panel_ignores_progress_from_an_earlier_poll(factory, tmp_path):
    from app.jobs.progress import ProgressReporter

    progress_path = tmp_path / "progress.json"
    ProgressReporter(progress_path).update("done", "old run")
    job = _RunningJob()
    job.started_at = dt.datetime(2999, 1, 1, tzinfo=dt.UTC)
    jobs = FakeJobRunner()
    jobs.jobs["poll"] = job
    settings = make_settings(poll_progress_file=str(progress_path))
    page = TestClient(create_app(factory, settings, jobs)).get("/jobs/poll").text
    assert "old run" not in page and "Starting the collector" in page


def _add_posting(factory, external_id, title, deadline, first_seen=None):
    with factory() as session:
        source = session.query(Source).first()
        session.add(
            Opportunity(
                source_id=source.id,
                external_id=external_id,
                title=title,
                company_name="Acme",
                deadline=deadline,
                first_seen_at=first_seen or dt.datetime.now(dt.UTC),
                relevance_score=10.0,
                raw={},
            )
        )
        session.commit()


def test_inbox_opens_on_open_postings_only(client, factory, posting_id):
    _add_posting(factory, "old", "Closed Role", dt.datetime(2000, 1, 1, tzinfo=dt.UTC))
    page = client.get("/").text
    assert "Backend Intern" in page and "Closed Role" not in page


def test_soonest_deadline_is_listed_first(client, factory, posting_id):
    soon = dt.datetime.now(dt.UTC) + dt.timedelta(days=2)
    _add_posting(factory, "soon", "Urgent Role", soon)
    page = client.get("/").text
    assert page.index("Urgent Role") < page.index("Backend Intern")


def test_postings_from_the_first_poll_are_not_new(client, factory, posting_id):
    with factory() as session:
        session.add(PollRun(status="success", finished_at=dt.datetime.now(dt.UTC)))
        session.commit()
    assert "new-flag" not in client.get("/").text


def test_fit_is_hidden_until_the_profile_is_filled_in(client, factory, posting_id):
    with factory() as session:
        session.get(Opportunity, posting_id).relevance_score = 10.0
        session.commit()
    assert 'class="fit ' not in client.get("/").text


def test_placeholder_location_is_hidden():
    from app.web.queries import clean_location

    assert clean_location(" . ") is None


def test_posting_links_to_superset_by_job_id(client, posting_id):
    page = client.get(f"/postings/{posting_id}").text
    assert "jobprofiles?currentJobId=job-1" in page


def test_dev_mode_is_off_by_default(client, posting_id):
    assert 'aria-checked="false"' in client.get("/").text


def test_dev_mode_toggle_sets_a_cookie_and_returns_to_the_page(client):
    response = client.post(
        "/dev-mode", data={"next": "/applications"}, follow_redirects=False
    )
    assert response.headers["location"] == "/applications"
    assert "dev_mode=1" in response.headers["set-cookie"]


def test_dev_mode_toggle_never_redirects_off_site(client):
    response = client.post(
        "/dev-mode", data={"next": "//evil.example"}, follow_redirects=False
    )
    assert response.headers["location"] == "/"


def test_dev_mode_shows_the_activity_link(client, posting_id):
    client.cookies.set("dev_mode", "1")
    assert 'href="/activity"' in client.get("/").text


def test_dev_mode_shows_the_inspector_on_a_posting(client, posting_id):
    client.cookies.set("dev_mode", "1")
    assert "Agent run inspector" in client.get(f"/postings/{posting_id}").text


def test_inspector_shows_recorded_tool_calls(factory, tmp_path, posting_id):
    from app.services.events import EventLog

    events_path = tmp_path / "events.jsonl"
    log = EventLog(events_path)
    log.record("agent", "route", "FULL · New posting", run_id="agent-1-1")
    log.record("tool", "web_search", '"Zensar" · 3 results', run_id="agent-1-1")
    with factory() as session:
        session.add(
            ResearchReport(
                opportunity_id=posting_id,
                kind="agent_brief",
                content={"markdown": "x", "run_id": "agent-1-1"},
                sources=[],
            )
        )
        session.commit()
    settings = make_settings(events_file=str(events_path))
    client = TestClient(create_app(factory, settings, FakeJobRunner()))
    page = client.get(f"/postings/{posting_id}/inspector").text
    assert "Zensar&#34; · 3 results" in page and "FULL · New posting" in page


def test_activity_page_lists_events(factory, tmp_path):
    from app.services.events import EventLog

    events_path = tmp_path / "events.jsonl"
    EventLog(events_path).record(
        "collector", "stage", "Reading announcements", run_id="poll-1"
    )
    settings = make_settings(events_file=str(events_path))
    client = TestClient(create_app(factory, settings, FakeJobRunner()))
    assert "Reading announcements" in client.get("/activity").text


def test_activity_can_be_filtered_by_source(factory, tmp_path):
    from app.services.events import EventLog

    events_path = tmp_path / "events.jsonl"
    log = EventLog(events_path)
    log.record("collector", "stage", "Reading announcements")
    log.record("ai", "request", "Claims request")
    settings = make_settings(events_file=str(events_path))
    client = TestClient(create_app(factory, settings, FakeJobRunner()))
    page = client.get("/activity/stream?source=ai").text
    assert "Claims request" in page and "Reading announcements" not in page
