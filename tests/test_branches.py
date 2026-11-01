"""Every BMSCE B.E. branch should be read from a resume and steer the fit score."""

from __future__ import annotations

import pytest

from app.services.branches import BRANCH_NAMES, branch_areas, find_branch
from app.services.matching import score_opportunity

SOFTWARE_JOB = {"title": "Software Development Engineer Intern"}
CORE_JOBS = {
    "Mechanical Engineering": "Mechanical Design Engineer",
    "Civil Engineering": "Structural Site Engineer",
    "Chemical Engineering": "Process Engineer Trainee",
    "Electrical and Electronics Engineering": "Power Electronics Intern",
    "Electronics and Communication Engineering": "VLSI Design Intern",
    "Aerospace Engineering": "Aerospace Structures Intern",
    "Biotechnology": "Bioinformatics Intern",
    "Industrial Engineering and Management": "Supply Chain Analyst Intern",
}


@pytest.mark.parametrize("branch", BRANCH_NAMES)
def test_every_branch_is_read_from_a_degree_line(branch):
    text = f"Asha Rao\nB.E. in {branch}, BMS College of Engineering, 2023 - 2027"
    assert find_branch(text) == branch


@pytest.mark.parametrize("branch", BRANCH_NAMES)
def test_every_branch_has_job_areas(branch):
    assert branch_areas(branch)


def test_short_branch_names_are_read():
    assert find_branch("B.E. (ECE), BMSCE, 2027") == "Electronics and Communication Engineering"


def test_skill_lines_do_not_decide_the_branch():
    assert find_branch("Skills: Biomedical Instrumentation, Chemical safety") is None


@pytest.mark.parametrize("branch, title", CORE_JOBS.items())
def test_a_branch_alone_marks_its_own_roles_as_suitable(branch, title):
    result = score_opportunity({"title": title}, {"branch": branch})
    assert "Role suits your skills" in result.reasons


@pytest.mark.parametrize("branch", ["Chemical Engineering", "Biotechnology", "Civil Engineering"])
def test_a_core_branch_without_software_skills_scores_software_roles_lower(branch):
    own = score_opportunity({"title": CORE_JOBS[branch]}, {"branch": branch}).score
    software = score_opportunity(SOFTWARE_JOB, {"branch": branch}).score
    assert own > software


def test_software_skills_still_count_for_a_circuit_branch():
    profile = {
        "branch": "Electronics and Communication Engineering",
        "skills": ["Python", "Java", "Data Structures"],
    }
    assert "Role suits your skills" in score_opportunity(SOFTWARE_JOB, profile).reasons


def test_no_branch_leaves_scoring_unchanged():
    profile = {"skills": ["Python", "Java"]}
    assert score_opportunity(SOFTWARE_JOB, profile).score == score_opportunity(
        SOFTWARE_JOB, {**profile, "branch": None}
    ).score
