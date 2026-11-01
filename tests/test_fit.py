"""Tests for scoring stored postings and keeping scores current."""

from __future__ import annotations

from app.models import Opportunity, Source
from app.services.fit import fit_payload, rescore_all
from app.services.preferences import update_matching


def _posting(session):
    source = Source(name="superset", base_url="https://app.joinsuperset.com")
    session.add(source)
    session.flush()
    record = Opportunity(
        source_id=source.id,
        external_id="job-1",
        title="Software Engineer Intern",
        company_name="Acme",
        location="Bengaluru",
        compensation="10 LPA",
        description="Required skills: Python, SQL",
        raw={
            "jd_documents": [
                {"name": "JD.pdf", "text": "Required skills: Python, Docker"}
            ]
        },
    )
    session.add(record)
    session.flush()
    return record


def test_jd_documents_feed_the_skills_used_for_scoring(session):
    record = _posting(session)
    assert "Docker" in fit_payload(record)["parsed_jd"]["skills"]


def test_the_parsed_jd_is_saved_on_the_posting(session):
    record = _posting(session)
    fit_payload(record)
    assert "parsed_jd" in record.raw


def test_rescoring_updates_every_posting_after_a_profile_change(session):
    record = _posting(session)
    update_matching(
        session,
        skills=["Python", "SQL"],
        technologies=["Docker"],
        locations=["Bengaluru"],
    )
    rescore_all(session)
    assert record.relevance_score >= 80
