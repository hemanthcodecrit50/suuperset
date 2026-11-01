"""Tests for turning a resume PDF into reviewed profile fields."""

from __future__ import annotations

import pytest
from resume_pdf import RESUME_LINES, make_pdf

from app.services.resume_import import ResumeReadError, extract_text, parse_resume

RESUME_TEXT = "\n".join(RESUME_LINES)


class FakeLlm:
    configured = True

    def __init__(self, answer: dict | None) -> None:
        self.answer = answer

    def complete_json(self, system: str, user: str, timeout: float = 0) -> dict | None:
        return self.answer


def test_text_is_read_from_a_real_pdf():
    assert "Order Service" in extract_text(make_pdf(RESUME_LINES))


def test_a_non_pdf_upload_is_rejected():
    with pytest.raises(ResumeReadError):
        extract_text(b"hello, this is not a pdf")


def test_a_pdf_without_readable_text_is_rejected():
    with pytest.raises(ResumeReadError):
        extract_text(make_pdf(["Scan"]))


def test_rules_find_the_degree():
    assert parse_resume(RESUME_TEXT, None).degree == "B.Tech"


def test_rules_find_the_cgpa():
    assert parse_resume(RESUME_TEXT, None).cgpa == 8.4


def test_rules_take_the_last_year_on_the_degree_line_as_graduation():
    assert parse_resume(RESUME_TEXT, None).graduation_year == 2027


def test_rules_find_known_technologies():
    assert "PostgreSQL" in parse_resume(RESUME_TEXT, None).technologies


def test_ai_skills_that_are_in_the_resume_are_kept():
    profile = parse_resume(
        RESUME_TEXT, FakeLlm({"skills": ["Python", "Data Structures"]})
    )
    assert profile.skills == ["Python", "Data Structures"]


def test_ai_skills_missing_from_the_resume_are_dropped():
    profile = parse_resume(RESUME_TEXT, FakeLlm({"skills": ["Python", "Kubernetes"]}))
    assert profile.skills == ["Python"] and "Kubernetes" in profile.dropped


def test_short_skill_names_need_a_whole_word():
    profile = parse_resume(RESUME_TEXT, FakeLlm({"skills": ["C", "R"]}))
    assert profile.skills == ["C"]


def test_an_invented_cgpa_is_dropped_and_the_real_one_used():
    profile = parse_resume(RESUME_TEXT, FakeLlm({"cgpa": 9.6}))
    assert profile.cgpa == 8.4


def test_projects_in_the_resume_are_kept_with_their_link():
    answer = {
        "projects": [{"name": "Order Service", "link": "github.com/asha/order-service"}]
    }
    project = parse_resume(RESUME_TEXT, FakeLlm(answer)).projects[0]
    assert project.link == "https://github.com/asha/order-service"


def test_projects_not_in_the_resume_are_dropped():
    answer = {"projects": [{"name": "Rocket Engine"}]}
    assert parse_resume(RESUME_TEXT, FakeLlm(answer)).projects == []


def test_links_not_in_the_resume_are_removed():
    answer = {
        "projects": [{"name": "Campus Navigator", "link": "https://evil.example/x"}]
    }
    assert parse_resume(RESUME_TEXT, FakeLlm(answer)).projects[0].link is None


def test_rules_are_used_when_the_model_gives_no_answer():
    profile = parse_resume(RESUME_TEXT, FakeLlm(None))
    assert profile.used_ai is False and profile.degree == "B.Tech"
