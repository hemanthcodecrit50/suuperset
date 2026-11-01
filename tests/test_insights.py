"""Tests for the AI's reading of profiles and JDs, and how scoring uses it."""

from __future__ import annotations

import pytest

from agent_helpers import make_settings
from app.models import Opportunity, Source
from app.services import jd_insight, profile_insight
from app.services.llm import LlmClient
from app.services.matching import score_opportunity
from app.services.preferences import (
    KEY_PROFILE_INSIGHT,
    active_insight,
    load_preferences,
    load_setting,
    update_matching,
)


class FakeLlm(LlmClient):
    def __init__(self, answer):
        super().__init__(settings=make_settings(openai_api_key="test"))
        self.answer = answer
        self.calls = 0

    def complete_json(self, system, user, timeout=0):
        self.calls += 1
        return self.answer


PROFILE_ANSWER = {
    "implied_skills": {
        "Spring Boot": ["Java", "REST APIs"],
        "Kubernetes": ["Docker"],
        "Cooking": ["Chef"],
    },
    "places": {"Karnataka": ["Bengaluru", "Mysuru", "Hubballi"], "Mars": ["Olympus"]},
    "anywhere": "yes",
    "role_fits": ["backend developer", "x" * 80],
    "role_misfits": ["sales executive"],
}


@pytest.fixture()
def profile(session):
    update_matching(
        session,
        skills=["Spring Boot"],
        technologies=["Kubernetes"],
        locations=["Karnataka"],
    )
    return session


# --- profile interpreter ------------------------------------------------


def test_profile_insight_keeps_suggestions_tied_to_listed_skills(profile):
    profile_insight.refresh_profile_insight(profile, FakeLlm(PROFILE_ANSWER))
    implied = load_setting(profile, KEY_PROFILE_INSIGHT)["implied_skills"]
    assert implied == {"Spring Boot": ["Java", "REST APIs"], "Kubernetes": ["Docker"]}


def test_places_are_kept_only_for_listed_locations(profile):
    profile_insight.refresh_profile_insight(profile, FakeLlm(PROFILE_ANSWER))
    assert list(load_setting(profile, KEY_PROFILE_INSIGHT)["places"]) == ["Karnataka"]


def test_anywhere_needs_a_real_true(profile):
    profile_insight.refresh_profile_insight(profile, FakeLlm(PROFILE_ANSWER))
    assert load_setting(profile, KEY_PROFILE_INSIGHT)["anywhere"] is False


def test_overlong_role_names_are_dropped(profile):
    profile_insight.refresh_profile_insight(profile, FakeLlm(PROFILE_ANSWER))
    assert load_setting(profile, KEY_PROFILE_INSIGHT)["role_fits"] == [
        "backend developer"
    ]


def test_an_unchanged_profile_is_not_sent_again(profile):
    llm = FakeLlm(PROFILE_ANSWER)
    profile_insight.refresh_profile_insight(profile, llm)
    profile_insight.refresh_profile_insight(profile, llm)
    assert llm.calls == 1


def test_a_changed_profile_is_read_again(profile):
    llm = FakeLlm(PROFILE_ANSWER)
    profile_insight.refresh_profile_insight(profile, llm)
    update_matching(profile, skills=["Spring Boot", "Go"])
    profile_insight.refresh_profile_insight(profile, llm)
    assert llm.calls == 2


def test_no_answer_keeps_the_previous_insight(profile):
    profile_insight.refresh_profile_insight(profile, FakeLlm(PROFILE_ANSWER))
    update_matching(profile, skills=["Spring Boot", "Go"])
    profile_insight.refresh_profile_insight(profile, FakeLlm(None))
    assert load_setting(profile, KEY_PROFILE_INSIGHT)["implied_skills"]


def test_a_removed_suggestion_is_not_used(profile):
    profile_insight.refresh_profile_insight(profile, FakeLlm(PROFILE_ANSWER))
    profile_insight.remove_insight_item(profile, "skill:Java")
    insight = load_preferences(profile).as_match_kwargs()["insight"]
    assert insight["implied_skills"]["Spring Boot"] == ["REST APIs"]


def test_removals_survive_a_new_reading(profile):
    profile_insight.refresh_profile_insight(profile, FakeLlm(PROFILE_ANSWER))
    profile_insight.remove_insight_item(profile, "place:Mysuru")
    update_matching(profile, skills=["Spring Boot", "Go"])
    profile_insight.refresh_profile_insight(profile, FakeLlm(PROFILE_ANSWER))
    assert (
        "Mysuru"
        not in active_insight(load_setting(profile, KEY_PROFILE_INSIGHT))["places"][
            "Karnataka"
        ]
    )


# --- scoring with insights ---------------------------------------------

INSIGHT = active_insight(
    {
        "implied_skills": {"Spring Boot": ["Java"]},
        "places": {"Karnataka": ["Mysuru"]},
        "role_fits": ["backend developer"],
        "role_misfits": ["sales executive"],
    }
)
STUDENT = {"skills": ["Spring Boot"], "locations": ["Karnataka"], "insight": INSIGHT}


def test_an_implied_skill_covers_a_jd_requirement():
    job = {"title": "Intern", "parsed_jd": {"skills": ["Java"]}}
    result = score_opportunity(job, STUDENT)
    assert (
        result.breakdown["skills_alignment"] == 0.35
        and "Matched via Spring Boot → java" in result.reasons
    )


def test_an_expanded_state_matches_a_city():
    job = {"title": "Intern", "location": "Mysuru", "compensation": "5 LPA"}
    assert (
        "Location matches your preferences" in score_opportunity(job, STUDENT).reasons
    )


def test_a_suited_role_from_the_ai_scores_full_role_fit():
    job = {"title": "Backend Engineer Intern"}
    assert score_opportunity(job, STUDENT).breakdown["role_alignment"] == 0.25


def test_a_role_outside_the_area_scores_low():
    job = {"title": "Sales Executive"}
    assert score_opportunity(job, STUDENT).breakdown["role_alignment"] == pytest.approx(
        0.075
    )


def test_generic_role_words_alone_do_not_match():
    job = {"title": "Graduate Engineer Trainee"}
    assert "Role suits your profile" not in score_opportunity(job, STUDENT).reasons


def test_jd_insight_skills_replace_the_rule_parser():
    job = {
        "title": "Intern",
        "parsed_jd": {"skills": ["adaptability across", "Python"]},
        "jd_insight": {"required_skills": ["Java", "SQL"]},
    }
    assert "Covers 1 of 2 JD skills" in score_opportunity(job, STUDENT).reasons


def test_scoring_without_any_insight_still_works():
    job = {"title": "Intern", "parsed_jd": {"skills": ["Java"]}}
    assert score_opportunity(job, {"skills": ["Java"]}).score > 0


# --- JD interpreter -----------------------------------------------------


@pytest.fixture()
def posting(session):
    source = Source(name="superset", base_url="https://app.joinsuperset.com")
    session.add(source)
    session.flush()
    record = Opportunity(
        source_id=source.id,
        external_id="job-1",
        title="Design Engineer",
        company_name="Acme",
        description="We need AutoCAD and SolidWorks. Knowledge of thermodynamics is a plus.",
        raw={},
    )
    session.add(record)
    session.flush()
    return record


JD_ANSWER = {
    "required_skills": ["AutoCAD", "SolidWorks", "Kubernetes"],
    "preferred_skills": ["Thermodynamics"],
    "role_area": "mechanical design",
    "role_keywords": ["design", "mechanical"],
}


def test_jd_skills_not_in_the_posting_are_dropped(posting):
    jd_insight.interpret_jd(posting, FakeLlm(JD_ANSWER))
    assert posting.raw["jd_insight"]["required_skills"] == ["AutoCAD", "SolidWorks"]


def test_a_read_jd_is_not_read_again(posting):
    jd_insight.interpret_jd(posting, FakeLlm(JD_ANSWER))
    assert jd_insight.needs_jd_insight(posting) is False


def test_a_changed_jd_is_read_again(posting):
    jd_insight.interpret_jd(posting, FakeLlm(JD_ANSWER))
    posting.description = "Now we need CATIA."
    assert jd_insight.needs_jd_insight(posting) is True


def test_no_answer_leaves_the_posting_unread(posting):
    assert (
        jd_insight.interpret_jd(posting, FakeLlm(None)) is False
        and "jd_insight" not in posting.raw
    )


def test_live_postings_are_read_first(session, posting):
    live = Opportunity(
        source_id=posting.source_id,
        external_id="job-2",
        title="Live",
        company_name="B",
        raw={"job_profile_status": "OPEN_FOR_APPLICATIONS"},
    )
    session.add(live)
    session.flush()
    assert jd_insight.postings_needing_insight(session, limit=1) == [live]


# --- the poll step ------------------------------------------------------


def test_poll_reads_at_most_the_configured_number_of_jds(session, posting, monkeypatch):
    from app.jobs import poll
    from app.jobs.progress import ProgressReporter

    for index in range(3):
        session.add(
            Opportunity(
                source_id=posting.source_id, external_id=f"x{index}", title="T", raw={}
            )
        )
    session.flush()
    monkeypatch.setattr(
        "app.services.llm.LlmClient.configured", property(lambda self: True)
    )
    monkeypatch.setattr(
        "app.services.llm.LlmClient.complete_json",
        lambda self, s, u, timeout=0: JD_ANSWER,
    )
    settings = make_settings(jd_insight_max_per_poll=2)
    assert poll._read_jds(session, settings, ProgressReporter(None)) == 2


def test_a_combined_requirement_is_met_by_either_part():
    job = {"title": "Intern", "jd_insight": {"required_skills": ["AI/ML"]}}
    result = score_opportunity(job, {"skills": ["Machine Learning"]})
    assert result.breakdown["skills_alignment"] == 0.35


def test_a_combined_requirement_is_shown_whole_when_missing():
    job = {"title": "Intern", "jd_insight": {"required_skills": ["TCP/IP", "Python"]}}
    assert "Missing: tcp/ip" in score_opportunity(job, {"skills": ["Python"]}).reasons
