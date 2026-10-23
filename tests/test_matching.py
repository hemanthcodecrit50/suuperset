from __future__ import annotations

from app.services.matching import evaluate_eligibility, score_opportunity


def test_score_opportunity_rewards_overlap():
    preferences = {
        "skills": ["python", "sql"],
        "interests": ["backend"],
        "technologies": ["python", "postgres"],
        "locations": ["remote", "bangalore"],
    }
    opportunity = {
        "title": "Backend Engineer",
        "role": "backend",
        "skills": ["Python", "SQL", "PostgreSQL"],
        "location": "Remote",
        "compensation": "INR 12 LPA",
    }
    result = score_opportunity(opportunity, preferences)
    assert 0 <= result.score <= 100
    assert result.score > 50


def test_missing_information_is_reported_unknown():
    result = score_opportunity({"title": "Some Role"}, {})
    assert "compensation" in result.unknown
    assert "skills" in result.unknown
    assert result.score >= 0


def test_eligibility_unknown_when_data_missing():
    met, unknown = evaluate_eligibility({"min_cgpa": 7.0}, {})
    assert met is None
    assert "cgpa" in unknown


def test_eligibility_rejects_low_cgpa():
    met, unknown = evaluate_eligibility({"min_cgpa": 7.0}, {"cgpa": 6.0})
    assert met is False
    assert unknown == []


def test_eligibility_passes_when_all_known():
    met, _ = evaluate_eligibility(
        {"min_cgpa": 7.0, "graduation_year": 2026},
        {"cgpa": 8.5, "graduation_year": 2026},
    )
    assert met is True
