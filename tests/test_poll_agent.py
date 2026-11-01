"""Tests for the agent hook in the poll job."""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_helpers import make_settings

from app.collector.job_profiles import RawOpportunity
from app.jobs import poll


class _Record:
    id = 7


def test_disabled_agent_returns_no_brief(session):
    settings = make_settings(agent_enabled=False)
    assert poll._agent_brief(session, settings, _Record(), "new", {}) == ""


def test_agent_failure_does_not_stop_collection(session, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("search is down")

    monkeypatch.setattr("app.agent.runner.analyze_opportunity", explode)
    assert poll._agent_brief(session, make_settings(), _Record(), "new", {}) == ""


def test_brief_is_appended_to_the_research_document(session, monkeypatch):
    written = []
    monkeypatch.setattr(poll, "_agent_brief", lambda *args: "## Agent brief\n")
    monkeypatch.setattr(
        poll,
        "write_document",
        lambda content, *args: written.append(content) or Path("x.md"),
    )
    opportunity = RawOpportunity(
        external_id="job-1", title="Backend Intern", company_name="Zensar"
    )
    result = poll._persist(
        session, make_settings(), [], [opportunity], queue_alerts=True
    )
    assert "## Agent brief" in written[0] and result.items_briefed == 1


def test_first_run_seeding_runs_no_agent(session, monkeypatch):
    monkeypatch.setattr(
        poll, "_agent_brief", lambda *args: pytest.fail("agent must not run")
    )
    monkeypatch.setattr(poll, "write_document", lambda *args: Path("x.md"))
    opportunity = RawOpportunity(
        external_id="job-1", title="Backend Intern", company_name="Zensar"
    )
    poll._persist(session, make_settings(), [], [opportunity], queue_alerts=False)


def test_briefs_per_run_are_capped(session, monkeypatch):
    monkeypatch.setattr(poll, "_agent_brief", lambda *args: "## Agent brief\n")
    monkeypatch.setattr(poll, "write_document", lambda *args: Path("x.md"))
    opportunities = [
        RawOpportunity(external_id=f"job-{n}", title=f"Role {n}", company_name="Zensar")
        for n in range(3)
    ]
    settings = make_settings(agent_max_briefs_per_run=2)
    result = poll._persist(session, settings, [], opportunities, queue_alerts=True)
    assert result.items_briefed == 2


def test_seeding_run_writes_no_documents(session, monkeypatch):
    monkeypatch.setattr(
        poll, "write_document", lambda *args: pytest.fail("no documents")
    )
    opportunity = RawOpportunity(
        external_id="job-1", title="Backend Intern", company_name="Zensar"
    )
    poll._persist(session, make_settings(), [], [opportunity], queue_alerts=False)


def test_research_document_never_calls_the_model(session, monkeypatch):
    calls = []
    monkeypatch.setattr(poll, "_agent_brief", lambda *args: "")
    monkeypatch.setattr(poll, "write_document", lambda *args: Path("x.md"))
    monkeypatch.setattr(
        poll,
        "build_document",
        lambda *args, **kwargs: calls.append(kwargs.get("use_model")) or "doc",
    )
    opportunity = RawOpportunity(
        external_id="job-1", title="Backend Intern", company_name="Zensar"
    )
    poll._persist(session, make_settings(), [], [opportunity], queue_alerts=True)
    assert calls == [False]


def test_interrupted_runs_are_closed(session):
    from app.models import PollRun

    session.add(PollRun(status="running"))
    session.flush()
    poll._close_interrupted_runs(session)
    assert session.query(PollRun).one().status == "interrupted"
