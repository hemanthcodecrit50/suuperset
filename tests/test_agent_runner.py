"""Tests for running the agent on stored opportunities."""

from __future__ import annotations

import datetime as dt

import pytest
from agent_helpers import FakeTools, make_deps, make_search_result, make_settings

from app.agent.runner import analyze_opportunity
from app.models import Opportunity, PortfolioItem, ResearchReport, Source

WIKI = "https://en.wikipedia.org/wiki/Zensar"
FACT = "Zensar Technologies is an Indian IT services company founded in 1963"
FUTURE = dt.datetime(2999, 1, 1, tzinfo=dt.UTC)


@pytest.fixture()
def tools() -> FakeTools:
    return FakeTools(make_settings(), default_results=[make_search_result(FACT, WIKI)])


@pytest.fixture()
def deps(tools):
    return make_deps(tools)


def _add_opportunity(
    session, external_id="job-1", title="Backend Intern", company="Zensar"
):
    source = session.query(Source).first() or Source(
        name="superset", base_url="https://app.joinsuperset.com"
    )
    session.add(source)
    session.flush()
    record = Opportunity(
        source_id=source.id,
        external_id=external_id,
        title=title,
        company_name=company,
        skills=["Python"],
        description="Required skills: Python, SQL\nHiring workflow: Online Assessment, Interview",
        compensation="12 LPA",
        deadline=FUTURE,
        content_hash=f"hash-{external_id}",
        raw={},
    )
    session.add(record)
    session.add(
        PortfolioItem(
            name="Order Service",
            description="Backend service built with Python",
            technologies=["Python"],
            status="implemented",
        )
    )
    session.flush()
    return record


def test_brief_cites_the_verified_source(session, deps):
    record = _add_opportunity(session)
    result = analyze_opportunity(session, record, "new", deps=deps)
    assert WIKI in result.markdown


def test_brief_is_saved_for_the_opportunity(session, deps):
    record = _add_opportunity(session)
    analyze_opportunity(session, record, "new", deps=deps)
    kinds = [
        row.kind
        for row in session.query(ResearchReport).filter_by(opportunity_id=record.id)
    ]
    assert kinds == ["agent_brief"]


def test_same_version_of_a_posting_is_not_analysed_twice(session, deps):
    record = _add_opportunity(session)
    analyze_opportunity(session, record, "new", deps=deps)
    second = analyze_opportunity(session, record, "new", deps=deps)
    assert second.is_reused is True


def test_reusing_a_brief_makes_no_new_searches(session, deps, tools):
    record = _add_opportunity(session)
    analyze_opportunity(session, record, "new", deps=deps)
    call_count = len(tools.calls)
    analyze_opportunity(session, record, "new", deps=deps)
    assert len(tools.calls) == call_count


def test_changed_posting_gets_a_fresh_brief(session, deps):
    record = _add_opportunity(session)
    analyze_opportunity(session, record, "new", deps=deps)
    record.content_hash = "hash-changed"
    changes = {"description": {"old": "a", "new": "b"}}
    second = analyze_opportunity(session, record, "changed", changes, deps=deps)
    assert second.is_reused is False


def test_company_evidence_is_cached_for_the_next_posting(session, deps, tools):
    analyze_opportunity(session, _add_opportunity(session), "new", deps=deps)
    tools.calls.clear()
    analyze_opportunity(
        session, _add_opportunity(session, "job-2", "Data Intern"), "new", deps=deps
    )
    queries = [
        arguments["query"] for name, arguments in tools.calls if name == "web_search"
    ]
    assert "Zensar" not in queries


def test_no_save_leaves_the_database_untouched(session, deps):
    record = _add_opportunity(session)
    analyze_opportunity(session, record, "new", deps=deps, should_save=False)
    assert session.query(ResearchReport).count() == 0


def test_dropped_item_stores_nothing(session, deps):
    record = _add_opportunity(session)
    record.deadline = dt.datetime(2000, 1, 1, tzinfo=dt.UTC)
    result = analyze_opportunity(session, record, "new", deps=deps)
    assert result.markdown == "" and session.query(ResearchReport).count() == 0


def test_parsed_jd_is_stored_on_the_opportunity(session, deps):
    record = _add_opportunity(session)
    analyze_opportunity(session, record, "new", deps=deps)
    assert "parsed_jd" in record.raw


def test_pay_findings_for_one_role_are_not_cached_for_another_role(session, tools):
    pay_result = make_search_result(
        "Zensar interns earn 9 LPA", "https://forum.test/pay", "web"
    )
    tools.search_results = {"Zensar Backend Intern salary": [pay_result]}
    tools.default_results = []
    deps = make_deps(tools)
    first = _add_opportunity(session)
    first.compensation = None
    analyze_opportunity(session, first, "new", deps=deps)
    second = analyze_opportunity(
        session, _add_opportunity(session, "job-2", "Sales Executive"), "new", deps=deps
    )
    assert "forum.test" not in second.markdown


def test_pay_finding_appears_for_the_role_it_was_searched_for(session, tools):
    pay_result = make_search_result(
        "Zensar interns earn 9 LPA", "https://forum.test/pay", "web"
    )
    tools.search_results = {"Zensar Backend Intern salary": [pay_result]}
    tools.default_results = []
    record = _add_opportunity(session)
    record.compensation = None
    result = analyze_opportunity(session, record, "new", deps=make_deps(tools))
    assert "forum.test" in result.markdown


def test_fresh_run_ignores_the_stored_brief(session, deps):
    record = _add_opportunity(session)
    analyze_opportunity(session, record, "new", deps=deps)
    second = analyze_opportunity(session, record, "new", deps=deps, should_reuse=False)
    assert second.is_reused is False


def test_saved_brief_writes_its_fit_score_to_the_posting(session, deps):
    record = _add_opportunity(session)
    result = analyze_opportunity(session, record, "new", deps=deps)
    assert record.relevance_score == result.state.fit["score"]


def test_no_save_stores_no_resume_suggestions(session, tools):
    from agent_helpers import FakeLlm

    from app.models import ResumeSuggestion

    deps = make_deps(tools, FakeLlm(bullet="Backend service built with Python"))
    record = _add_opportunity(session)
    analyze_opportunity(session, record, "new", deps=deps, should_save=False)
    assert session.query(ResumeSuggestion).count() == 0
