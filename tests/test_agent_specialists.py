"""Tests for the matcher and resume advisor nodes."""

from __future__ import annotations

from agent_helpers import FakeLlm, make_deps, make_memory, make_state

from app.agent.matcher import match
from app.agent.resume_advisor import advise_resume


def test_matcher_marks_a_student_below_the_cgpa_cutoff_not_eligible():
    state = make_state(parsed_jd={"min_cgpa": 9.0})
    assert match(state, make_deps()).fit["eligibility"] == "not eligible"


def test_matcher_marks_a_student_meeting_the_cutoff_eligible():
    state = make_state(parsed_jd={"min_cgpa": 7.0})
    assert match(state, make_deps()).fit["eligibility"] == "eligible"


def test_matcher_reports_unknown_eligibility_when_the_profile_lacks_the_data():
    state = make_state(
        parsed_jd={"min_cgpa": 7.0}, memory=make_memory(match_profile={})
    )
    assert "cgpa" in match(state, make_deps()).fit["eligibility"]


def test_advisor_leads_with_the_project_that_covers_the_jd():
    state = advise_resume(
        make_state(parsed_jd={"skills": ["Python", "PostgreSQL"]}), make_deps()
    )
    assert state.resume_advice["lead_with"][0]["name"] == "Order Service"


def test_advisor_never_uses_a_planned_project():
    state = advise_resume(
        make_state(parsed_jd={"skills": ["Rust"]}, skills=[]), make_deps()
    )
    assert state.resume_advice["lead_with"] == []


def test_advisor_flags_a_skill_with_no_evidence_as_a_gap():
    state = advise_resume(
        make_state(parsed_jd={"skills": ["Kubernetes"]}, skills=[]), make_deps()
    )
    assert state.resume_advice["gaps"][0]["requirement"] == "Kubernetes"


def test_advisor_tells_the_student_not_to_claim_a_missing_skill():
    state = advise_resume(
        make_state(parsed_jd={"skills": ["Kubernetes"]}, skills=[]), make_deps()
    )
    assert "do not claim" in state.resume_advice["gaps"][0]["advice"]


def test_advisor_says_when_a_profile_skill_lacks_project_evidence():
    memory = make_memory(match_profile={"skills": ["kubernetes"]})
    state = advise_resume(
        make_state(parsed_jd={"skills": ["Kubernetes"]}, memory=memory, skills=[]),
        make_deps(),
    )
    assert state.resume_advice["gaps"][0]["in_profile"] is True


def test_advisor_without_a_model_proposes_no_bullets():
    state = advise_resume(make_state(parsed_jd={"skills": ["Python"]}), make_deps())
    assert state.resume_advice["bullet_rewrites"] == []


def test_advisor_with_a_model_proposes_a_bullet_for_the_best_project():
    llm = FakeLlm(bullet="Built a Python order service")
    state = advise_resume(
        make_state(parsed_jd={"skills": ["Python"]}), make_deps(llm=llm)
    )
    assert state.resume_advice["bullet_rewrites"][0]["project_id"] == 1


def test_advisor_records_an_unknown_when_the_jd_lists_no_skills():
    state = advise_resume(make_state(parsed_jd={}, skills=[]), make_deps())
    assert state.resume_advice is None and state.unknowns
