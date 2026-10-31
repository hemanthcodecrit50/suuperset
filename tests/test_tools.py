"""Tests for the agent tool registry and wrappers (week 1)."""

from __future__ import annotations

import datetime as dt

import pytest

from app.agent import tools
from app.config import Settings
from app.models import (
    AlertDelivery,
    Application,
    Opportunity,
    OpportunityVersion,
    PortfolioItem,
    Source,
)
from app.services import research
from app.services.pages import FetchedPage
from app.services.preferences import update_matching, update_notifications
from app.services.research import Evidence


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        database_url="sqlite://",
        telegram_bot_token="",
        telegram_chat_id="",
        smtp_host="",
        smtp_from="",
        alert_email_to="",
        search_backend="wikipedia",
        search_cache_dir=".cache/test-search",
        search_cache_ttl_hours=0,
    )


@pytest.fixture()
def opportunity(session):
    source = Source(name="superset", base_url="https://app.joinsuperset.com")
    session.add(source)
    session.flush()
    now = dt.datetime.now(dt.timezone.utc)
    record = Opportunity(
        source_id=source.id,
        external_id="job-1",
        title="Backend Engineer Intern",
        company_name="Zensar",
        location="Pune",
        work_type="Internship",
        compensation="12 LPA",
        eligibility="B.Tech, minimum CGPA 7.0, batch 2027",
        skills=["Python", "SQL"],
        description=(
            "You will build backend services with Python and PostgreSQL.\n"
            "Required skills: Python, SQL, Docker"
        ),
        deadline=dt.datetime(2026, 11, 30, tzinfo=dt.timezone.utc),
        apply_url="https://app.joinsuperset.com/students/jobprofiles?currentJobId=job-1",
        content_hash="hash-1",
        first_seen_at=now,
        last_seen_at=now,
        raw={"application_status": "APPLIED"},
    )
    session.add(record)
    session.flush()
    return record


@pytest.fixture()
def profile(session, opportunity):
    update_matching(
        session,
        skills=["python", "sql"],
        interests=["backend"],
        technologies=["python", "postgresql"],
        locations=["pune"],
        degree="B.Tech",
        cgpa=8.0,
        graduation_year=2027,
    )
    update_notifications(session, min_alert_score=0)
    session.add(
        PortfolioItem(
            name="Placement API",
            description="Built a FastAPI service with PostgreSQL and Docker.",
            technologies=["Python", "FastAPI", "PostgreSQL"],
            achievements=["Cut response time by 40%"],
            evidence_url="https://github.com/me/placement-api",
            status="completed",
        )
    )
    session.add(
        PortfolioItem(
            name="Portfolio site",
            description="A static React portfolio.",
            technologies=["React"],
            evidence_url="https://me.example.com",
            status="implemented",
        )
    )
    session.add(Application(opportunity_id=opportunity.id, status="applied"))
    session.flush()


def call(name, args, session, settings):
    return tools.call_tool(
        name, args, tools.ToolContext(session=session, settings=settings)
    )


def test_registry_exposes_all_planned_tools():
    names = {spec["name"] for spec in tools.tool_specs()}
    assert {
        "get_opportunity",
        "parse_jd",
        "web_search",
        "fetch_page",
        "score_fit",
        "rank_portfolio",
        "send_alert",
    }.issubset(names)
    for schema in tools.openai_tool_schemas():
        assert schema["type"] == "function"
        assert schema["function"]["parameters"]["type"] == "object"


def test_get_opportunity_returns_jd_and_application(
    session, opportunity, profile, settings
):
    result = call(
        "get_opportunity", {"opportunity_id": opportunity.id}, session, settings
    )
    assert result["ok"] is True
    data = result["result"]
    assert data["title"] == "Backend Engineer Intern"
    assert data["company"] == "Zensar"
    assert data["deadline"].startswith("2026-11-30")
    assert data["application"]["status"] == "applied"
    assert data["versions"] == []


def test_get_opportunity_reports_version_changes(session, opportunity, settings):
    session.add(
        OpportunityVersion(
            opportunity_id=opportunity.id,
            content_hash="old",
            title=opportunity.title,
            description=opportunity.description,
            eligibility=opportunity.eligibility,
            deadline=dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc),
            skills=["Python"],
        )
    )
    session.flush()

    result = call(
        "get_opportunity", {"opportunity_id": opportunity.id}, session, settings
    )
    assert "deadline" in result["result"]["changes"]
    assert len(result["result"]["versions"]) == 1


def test_parse_jd_from_stored_opportunity_persists(session, opportunity, settings):
    result = call(
        "parse_jd",
        {"opportunity_id": opportunity.id, "persist": True},
        session,
        settings,
    )
    assert result["ok"] is True
    parsed = result["result"]
    assert "Python" in parsed["skills"]
    assert "SQL" in parsed["skills"]
    assert parsed["pay"]["amount"] == 12
    assert parsed["deadline"] == "2026-11-30"
    assert parsed["degree"] == "B.Tech"

    session.refresh(opportunity)
    assert opportunity.raw["parsed_jd"]["deadline"] == "2026-11-30"


def test_parse_jd_accepts_inline_text_without_a_session(settings):
    result = tools.call_tool(
        "parse_jd",
        {"text": "Required skills: Go, Kubernetes. Compensation: 20 LPA"},
        tools.ToolContext(settings=settings),
    )
    assert result["ok"] is True
    assert "Go" in result["result"]["skills"]
    assert "Kubernetes" in result["result"]["skills"]


def test_score_fit_uses_preferences_and_persists(session, opportunity, profile, settings):
    result = call(
        "score_fit",
        {"opportunity_id": opportunity.id, "persist": True},
        session,
        settings,
    )
    assert result["ok"] is True
    data = result["result"]
    assert 0 <= data["score"] <= 100
    assert data["hard_constraints_met"] is True
    session.refresh(opportunity)
    assert opportunity.relevance_score == data["score"]
    assert opportunity.eligible is True


def test_rank_portfolio_returns_real_items_with_evidence(
    session, opportunity, profile, settings
):
    result = call(
        "rank_portfolio",
        {"opportunity_id": opportunity.id},
        session,
        settings,
    )
    assert result["ok"] is True
    ranked = result["result"]["ranked"]
    assert ranked
    assert ranked[0]["name"] == "Placement API"
    assert ranked[0]["evidence_url"].startswith("https://github.com/")
    assert ranked[0]["score"] > 0


def test_rank_portfolio_without_requirements_is_honest(session, settings):
    result = call(
        "rank_portfolio",
        {"requirements": [], "portfolio_items": []},
        session,
        settings,
    )
    assert result["ok"] is True
    assert result["result"]["ranked"] == []
    assert "nothing is ranked" in result["result"]["note"]


def test_send_alert_is_deduplicated(session, opportunity, settings):
    first = call(
        "send_alert",
        {
            "title": "New role",
            "message": "Apply to Zensar",
            "channel": "dashboard",
            "opportunity_id": opportunity.id,
            "dedup_key": "test:1",
        },
        session,
        settings,
    )
    second = call(
        "send_alert",
        {
            "title": "New role",
            "message": "Apply to Zensar",
            "channel": "dashboard",
            "opportunity_id": opportunity.id,
            "dedup_key": "test:1",
        },
        session,
        settings,
    )
    assert first["result"]["queued"] is True
    assert second["result"]["queued"] is False
    rows = session.query(AlertDelivery).all()
    assert len(rows) == 1
    assert rows[0].channel == "dashboard"


def test_unknown_tool_returns_error_envelope(settings):
    result = tools.call_tool("no_such_tool", {}, tools.ToolContext(settings=settings))
    assert result["ok"] is False
    assert "Unknown tool" in result["error"]


def test_invalid_arguments_type_is_rejected(settings):
    result = tools.call_tool("parse_jd", ["not", "an", "object"], tools.ToolContext(settings=settings))
    assert result["ok"] is False
    assert "JSON object" in result["error"]


def test_tool_requiring_a_session_fails_clearly(settings):
    result = tools.call_tool(
        "get_opportunity", {"opportunity_id": 1}, tools.ToolContext(settings=settings)
    )
    assert result["ok"] is False
    assert "database session" in result["error"]


def test_web_search_wraps_the_backend(monkeypatch, settings):
    class FakeBackend:
        def search(self, query, limit=5):
            return [
                Evidence(
                    title="Zensar",
                    claim="Zensar is an IT services company.",
                    source_url="https://en.wikipedia.org/wiki/Zensar",
                    source_type="reference",
                )
            ]

    monkeypatch.setattr(
        tools.search, "default_search_backend", lambda _settings=None: FakeBackend()
    )
    result = tools.call_tool(
        "web_search",
        {"company": "Zensar", "role": "Backend Intern"},
        tools.ToolContext(settings=settings),
    )
    assert result["ok"] is True
    assert result["result"]["count"] == 1
    assert result["result"]["results"][0]["source_type"] == "reference"
    assert len(result["result"]["queries"]) >= 4


def test_fetch_page_wraps_the_fetcher(monkeypatch, settings):
    def fake_fetch(url, **kwargs):
        return FetchedPage(
            url=url,
            final_url=url,
            status=200,
            title="About",
            text="Zensar builds software.",
            content_type="text/html",
            bytes_read=100,
            chars=24,
            truncated=False,
            fetched_at="2026-10-31T00:00:00+00:00",
        )

    monkeypatch.setattr(tools.pages, "fetch_page", fake_fetch)
    result = tools.call_tool(
        "fetch_page",
        {"url": "https://www.zensar.com/about"},
        tools.ToolContext(settings=settings),
    )
    assert result["ok"] is True
    assert result["result"]["text"] == "Zensar builds software."


def test_fetch_page_blocked_domain_becomes_tool_error(monkeypatch, settings):
    from app.services.pages import PageFetchError

    def fake_fetch(url, **kwargs):
        raise PageFetchError("Host 'x.test' is not on the fetch allow-list.")

    monkeypatch.setattr(tools.pages, "fetch_page", fake_fetch)
    result = tools.call_tool(
        "fetch_page",
        {"url": "https://x.test/"},
        tools.ToolContext(settings=settings),
    )
    assert result["ok"] is False
    assert "allow-list" in result["error"]


def test_research_company_wraps_the_service(monkeypatch, settings):
    def fake_generate(company, role=None, force=False):
        return research.ResearchReport(
            company=company,
            summary="A sourced summary.",
            sections={"overview": "Overview from sources."},
            evidence=[
                Evidence(
                    title="Official",
                    claim="Official website",
                    source_url="https://www.zensar.com",
                    source_type="official",
                )
            ],
            model="test-model",
            generated_at=dt.datetime(2026, 10, 31, tzinfo=dt.timezone.utc),
        )

    monkeypatch.setattr(tools.research, "generate_and_store", fake_generate)
    result = tools.call_tool(
        "research_company",
        {"company": "Zensar", "role": "Backend Intern"},
        tools.ToolContext(settings=settings),
    )
    assert result["ok"] is True
    assert result["result"]["summary"] == "A sourced summary."
    assert result["result"]["evidence"][0]["source_type"] == "official"


def test_student_profile_returns_preferences_and_portfolio(
    session, profile, settings
):
    result = call("get_student_profile", {}, session, settings)
    assert result["ok"] is True
    data = result["result"]
    assert data["configured"] is True
    assert data["matching"]["degree"] == "B.Tech"
    assert data["portfolio_count"] == 2
    assert all("evidence_url" in item for item in data["portfolio"])
