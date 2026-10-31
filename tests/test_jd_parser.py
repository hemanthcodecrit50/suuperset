"""Tests for the deterministic JD parser (week 1)."""

from __future__ import annotations

import pytest

from app.services.jd_parser import parse_jd, parse_pay

STRUCTURED_JD = """
Opening Overview
Role
Job Functions: Engineering
Location: Bengaluru
Work Type: Internship

Job Description
You will build backend services with Python and PostgreSQL.

Hiring Workflow
1. Online Assessment
2. Technical Interview
3. HR Round

Compensation
Effective 125,000 INR (month)

Eligibility Criteria
B.Tech or M.Tech, minimum CGPA 7.0
Batch: 2027

Deadline: 20 Oct 2026

Bond: 12 months service agreement of INR 100,000

Required skills: Python, SQL, Docker

Additional Questions
Why do you want to join?
"""


def test_parses_structured_jd():
    parsed = parse_jd(STRUCTURED_JD).to_dict()

    assert parsed["skills"][:3] == ["Python", "SQL", "Docker"]
    assert "Python" in parsed["skills"]
    assert parsed["rounds"] == [
        "Online Assessment",
        "Technical Interview",
        "HR Round",
    ]
    assert parsed["bond"]["applicable"] is True
    assert parsed["bond"]["months"] == 12
    assert parsed["bond"]["amount"] == 100000
    assert parsed["bond"]["currency"] == "INR"
    assert parsed["pay"]["amount"] == 125000
    assert parsed["pay"]["currency"] == "INR"
    assert parsed["pay"]["interval"] == "month"
    assert parsed["location"] == "Bengaluru"
    assert parsed["work_type"] == "Internship"
    assert parsed["degree"] == "B.Tech"
    assert parsed["min_cgpa"] == 7.0
    assert parsed["graduation_year"] == 2027
    assert parsed["deadline"] == "2026-10-20"
    assert parsed["experience"] is None
    assert "experience" in parsed["unknown_fields"]
    assert "deadline" not in parsed["unknown_fields"]


def test_unknown_fields_stay_null():
    parsed = parse_jd("We are a great place to work. Apply soon.").to_dict()

    assert parsed["skills"] is None
    assert parsed["rounds"] is None
    assert parsed["bond"] is None
    assert parsed["pay"] is None
    assert parsed["deadline"] is None
    assert parsed["eligibility"] is None
    assert set(
        ["skills", "rounds", "bond", "pay", "eligibility", "deadline"]
    ).issubset(parsed["unknown_fields"])


def test_empty_jd_is_all_unknown():
    parsed = parse_jd("   ").to_dict()
    assert parsed["skills"] is None
    assert parsed["pay"] is None
    assert len(parsed["unknown_fields"]) == 12


def test_no_bond_is_reported_as_not_applicable():
    parsed = parse_jd("This role has no bond or service agreement.").to_dict()
    assert parsed["bond"]["applicable"] is False
    assert parsed["bond"]["months"] is None


def test_team_bonding_is_not_a_bond():
    parsed = parse_jd("We value team bonding, offsites and collaboration.").to_dict()
    assert parsed["bond"] is None


def test_accepts_html_and_extracts_text():
    html = (
        "<h2>Required skills</h2><p>Java, Spring Boot, AWS</p>"
        "<h2>Compensation</h2><p>18 LPA</p>"
    )
    parsed = parse_jd(html, title="Backend Intern").to_dict()
    assert "Java" in parsed["skills"]
    assert "Spring Boot" in parsed["skills"]
    assert "AWS" in parsed["skills"]
    assert parsed["pay"]["amount"] == 18
    assert parsed["work_type"] == "Internship"


def test_deadline_formats():
    for text, expected in (
        ("Application deadline: 2027-01-05", "2027-01-05"),
        ("Last date to apply 05/01/2027", "2027-01-05"),
        ("Applications close on 5 January 2027", "2027-01-05"),
        ("Closes on Jan 5, 2027", "2027-01-05"),
    ):
        assert parse_jd(text).deadline == expected


@pytest.mark.parametrize(
    ("text", "amount", "currency", "interval"),
    [
        ("12 LPA", 12, "INR", "annum"),
        ("₹ 8,50,000 per annum", 850000, "INR", "annum"),
        ("Effective 125,000 INR (month)", 125000, "INR", "month"),
        ("Stipend: INR 50,000 per month", 50000, "INR", "month"),
        ("$120,000 per year", 120000, "USD", "annum"),
    ],
)
def test_pay_variants(text, amount, currency, interval):
    pay = parse_pay(text)
    assert pay is not None
    assert pay.amount == amount
    assert pay.currency == currency
    assert pay.interval == interval


def test_competitive_salary_does_not_invent_a_number():
    assert parse_pay("Competitive salary as per industry standards") is None


def test_rounds_fallback_without_section():
    parsed = parse_jd(
        "Selection: Round 1 aptitude test, Round 2 coding interview, Round 3 HR."
    )
    assert parsed.rounds is not None
    assert any("aptitude" in item.lower() for item in parsed.rounds)
