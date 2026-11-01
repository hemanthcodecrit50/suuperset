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


# --- optimistic, JD-centred fit -----------------------------------------

NVIDIA_JD = {
    "title": "System Software Engineering Intern",
    "location": "Pune / Bangalore / Hyderabad",
    "compensation": "1,00,000 per month",
    "parsed_jd": {
        "skills": ["Python", "Java", "C++", "Linux", "Data Structures", "Algorithms"]
    },
}
STUDENT = {
    "skills": ["Python", "C", "Java", "SQL", "Data Structures", "Algorithms"],
    "technologies": ["Linux", "Docker", "Git", "React"],
    "locations": ["Bengaluru"],
}


def test_covering_most_jd_skills_gives_a_high_score():
    assert score_opportunity(NVIDIA_JD, STUDENT).score >= 80


def test_listing_more_skills_never_lowers_the_score():
    richer = {
        **STUDENT,
        "skills": STUDENT["skills"] + ["Kotlin", "Rust", "Photography", "R"],
    }
    assert (
        score_opportunity(NVIDIA_JD, richer).score
        >= score_opportunity(NVIDIA_JD, STUDENT).score
    )


def test_missing_jd_skills_are_named():
    assert "Missing: c++" in score_opportunity(NVIDIA_JD, STUDENT).reasons


def test_a_short_skill_does_not_match_inside_another():
    job = {"title": "Intern", "parsed_jd": {"skills": ["React"]}}
    assert score_opportunity(job, {"skills": ["C"]}).breakdown["skills_alignment"] == 0


def test_bangalore_matches_bengaluru():
    assert (
        "Location matches your preferences"
        in score_opportunity(NVIDIA_JD, STUDENT).reasons
    )


def test_pan_india_matches_any_preferred_city():
    job = {**NVIDIA_JD, "location": "PAN India"}
    assert (
        "Location matches your preferences" in score_opportunity(job, STUDENT).reasons
    )


def test_a_job_with_no_known_skills_is_scored_neutrally_not_zero():
    job = {
        "title": "Graduate Trainee",
        "compensation": "4 LPA",
        "location": "Bengaluru",
    }
    assert score_opportunity(job, STUDENT).score >= 60


def test_hobby_interests_do_not_pull_the_score_down():
    with_hobbies = {**STUDENT, "interests": ["Photography", "Travel", "Music"]}
    assert (
        score_opportunity(NVIDIA_JD, with_hobbies).score
        == score_opportunity(NVIDIA_JD, STUDENT).score
    )


def test_a_role_outside_the_students_skills_scores_lower():
    mechanical = {
        **NVIDIA_JD,
        "title": "Mechanical Design Engineer",
        "parsed_jd": {"skills": []},
    }
    software = {**NVIDIA_JD, "title": "Software Engineer", "parsed_jd": {"skills": []}}
    assert (
        score_opportunity(mechanical, STUDENT).score
        < score_opportunity(software, STUDENT).score
    )


def test_be_satisfies_a_btech_requirement():
    met, _ = evaluate_eligibility(
        {"degree": "B.Tech"}, {"degree": "Bachelor of Engineering"}
    )
    assert met is True


def test_a_different_degree_family_is_not_eligible():
    met, _ = evaluate_eligibility({"degree": "MBA"}, {"degree": "B.E."})
    assert met is False


def test_an_unrecognised_degree_is_unknown_not_rejected():
    met, unknown = evaluate_eligibility(
        {"degree": "B.Arch"}, {"degree": "Bachelor of Engineering"}
    )
    assert met is None and "degree" in unknown


def test_superset_saying_eligible_overrides_a_misread_jd():
    job = {"eligibility": "Eligible", "graduation_year": 2026, "degree": "M.Tech"}
    met, _ = evaluate_eligibility(job, {"graduation_year": 2027, "degree": "B.E."})
    assert met is True


def test_superset_saying_not_eligible_is_respected():
    met, _ = evaluate_eligibility({"eligibility": "Not eligible"}, {"cgpa": 9.5})
    assert met is False


def test_an_impossible_cgpa_cutoff_is_ignored():
    met, _ = evaluate_eligibility({"min_cgpa": 10.0}, {"cgpa": 9.46})
    assert met is True


def test_prose_mistaken_for_skills_does_not_count_as_missing():
    job = {
        "title": "Developer",
        "parsed_jd": {"skills": ["Python", "adaptability across", "industries"]},
    }
    assert (
        score_opportunity(job, {"skills": ["Python"]}).breakdown["skills_alignment"]
        == 0.35
    )
