"""The fit score should behave sensibly for very different students, not just one profile."""

from __future__ import annotations

import pytest

from app.services.matching import evaluate_eligibility, score_opportunity

JOBS = {
    "software": {
        "title": "Software Development Engineer Intern",
        "location": "Bengaluru",
        "compensation": "50,000 per month",
        "parsed_jd": {"skills": ["Python", "Java", "SQL", "Data Structures"]},
    },
    "data": {
        "title": "Data Scientist Intern",
        "location": "Hyderabad",
        "compensation": "40,000 per month",
        "parsed_jd": {"skills": ["Python", "SQL", "Machine Learning", "Pandas"]},
    },
    "mechanical": {
        "title": "Mechanical Design Engineer",
        "location": "Pune",
        "compensation": "6 LPA",
        "parsed_jd": {"skills": ["AutoCAD", "SolidWorks", "ANSYS"]},
    },
    "electronics": {
        "title": "VLSI Design Intern",
        "location": "Bengaluru",
        "compensation": "35,000 per month",
        "parsed_jd": {"skills": ["Verilog", "VHDL", "Embedded C"]},
    },
    "business": {
        "title": "Marketing Analyst",
        "location": "Mumbai",
        "compensation": "7 LPA",
        "parsed_jd": {"skills": ["Excel", "Power BI", "SEO"]},
    },
    "trainee": {"title": "Graduate Trainee", "location": "PAN India", "compensation": "4 LPA"},
}
STUDENTS = {
    "software": {
        "skills": ["Python", "Java", "JavaScript", "Data Structures", "Algorithms"],
        "technologies": ["React", "SQL", "Git", "Docker"],
        "locations": ["Bangalore"],
    },
    "data": {
        "skills": ["Python", "SQL", "Statistics", "Machine Learning"],
        "technologies": ["Pandas", "TensorFlow", "Scikit-learn", "NumPy"],
        "locations": ["Hyderabad"],
    },
    "mechanical": {
        "skills": ["Thermodynamics", "Fluid Mechanics", "Manufacturing"],
        "technologies": ["AutoCAD", "SolidWorks", "ANSYS", "MATLAB"],
        "locations": ["Pune"],
    },
    "electronics": {
        "skills": ["C", "Embedded C", "Digital Electronics"],
        "technologies": ["Verilog", "VHDL", "Arduino", "Raspberry Pi"],
        "locations": ["Bengaluru"],
    },
    "business": {
        "skills": ["Marketing", "Communication", "Finance"],
        "technologies": ["Excel", "Power BI", "Tableau", "SEO"],
        "locations": ["Bombay"],
    },
}


def _scores(student: dict) -> dict[str, float]:
    return {name: score_opportunity(job, student).score for name, job in JOBS.items()}


@pytest.mark.parametrize("field", sorted(STUDENTS))
def test_each_student_scores_their_own_field_highest_among_specialist_jobs(field):
    specialist = {name: score for name, score in _scores(STUDENTS[field]).items() if name != "trainee"}
    assert max(specialist, key=specialist.get) == field


@pytest.mark.parametrize("field", sorted(STUDENTS))
def test_each_student_gets_a_strong_score_in_their_own_field(field):
    assert _scores(STUDENTS[field])[field] >= 80


@pytest.mark.parametrize("field", sorted(STUDENTS))
def test_a_generic_trainee_role_is_never_scored_low(field):
    assert _scores(STUDENTS[field])["trainee"] >= 60


@pytest.mark.parametrize("field", sorted(STUDENTS))
def test_scores_stay_between_0_and_100(field):
    assert all(0 <= score <= 100 for score in _scores(STUDENTS[field]).values())


def test_an_empty_profile_scores_every_job_neutrally():
    scores = _scores({})
    assert min(scores.values()) >= 50 and max(scores.values()) - min(scores.values()) <= 15


def test_a_profile_with_one_skill_still_works():
    assert _scores({"skills": ["Python"]})["software"] > _scores({"skills": ["Python"]})["mechanical"]


def test_javascript_does_not_count_as_java():
    job = {"title": "Java Developer", "parsed_jd": {"skills": ["Java"]}}
    assert score_opportunity(job, {"skills": ["JavaScript"]}).breakdown["skills_alignment"] == 0


def test_a_combined_skill_like_c_cpp_counts_for_each_part():
    job = {"title": "Embedded Engineer", "parsed_jd": {"skills": ["C++"]}}
    assert score_opportunity(job, {"skills": ["C/C++"]}).breakdown["skills_alignment"] > 0


def test_preferring_india_accepts_any_city():
    job = {"title": "Engineer", "location": "Mysuru", "compensation": "5 LPA"}
    reasons = score_opportunity(job, {"skills": ["x"], "locations": ["India"]}).reasons
    assert "Location matches your preferences" in reasons


@pytest.mark.parametrize(
    ("required", "held", "expected"),
    [
        ("B.Tech", "B.E. Mechanical", True),
        ("B.E.", "Bachelor of Technology", True),
        ("MBA", "MBA (Marketing)", True),
        ("M.Tech", "B.E.", False),
        ("B.Arch", "B.E.", None),
        ("MCA", "BCA", False),
    ],
)
def test_degree_families_across_streams(required, held, expected):
    met, _ = evaluate_eligibility({"degree": required}, {"degree": held})
    assert met is expected
