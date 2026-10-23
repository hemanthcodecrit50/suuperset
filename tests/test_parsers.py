"""Tests for the screenshot-derived text parsers.

Sample inputs below are reconstructed from the OCR of the screenshots in
sample_images/ so the parsers are exercised against realistic portal text.
"""

from __future__ import annotations

from app.collector.job_profiles import parse_detail_panel, parse_listing_text

CARD_TEXT = """Application Developer - Associate
Thoughtworks | Full Time | PAN India
Thoughtworks · PAN India
Applied
3 days ago
You applied with resume : resume_to_google."""

CLOSING_CARD_TEXT = """AI/ML Developer — GenAI - First Engineering
SomeCompany | Intern | Vadodara/Anand
Yet to apply
3 days ago · Closes in 13 days"""

PANEL_TEXT = """Opening Overview
Category: Tier 2
Role: Application Developer
Job Functions: Engineering
Job Profile CTC: 1110000 per Annum
Other Info: 1 year service bond
Job Description
Greetings from Thoughtworks!
We are pleased to invite your esteemed institution to participate in the
Thoughtworks 2027 Graduate Hiring Program.
Eligibility Criteria
B.E/B.Tech - all branches, CGPA >= 7.0
Hiring Workflow
Round 1: Online assessment, Round 2: Interviews
Additional Questions
Are you willing to relocate?
Required skills
Python, SQL, System Design"""


def test_listing_card_fields():
    raw = parse_listing_text(CARD_TEXT)
    assert raw.title == "Application Developer - Associate"
    assert raw.company_name == "Thoughtworks"
    assert raw.work_type == "Full Time"
    assert raw.location == "PAN India"
    assert raw.status == "Applied"


def test_listing_closes_in_days():
    raw = parse_listing_text(CLOSING_CARD_TEXT)
    assert raw.closes_in_days == 13
    assert raw.status == "Yet To Apply"


def test_detail_panel_metadata():
    detail = parse_detail_panel(PANEL_TEXT)
    assert detail["category"] == "Tier 2"
    assert detail["job_function"] == "Engineering"
    assert detail["compensation"] == "1110000 per Annum"
    assert detail["other_info"] == "1 year service bond"
    assert detail["skills"] == ["Python", "SQL", "System Design"]


def test_detail_panel_sections():
    detail = parse_detail_panel(PANEL_TEXT)
    jd = detail["sections"]["Job Description"]
    assert "Thoughtworks 2027 Graduate Hiring Program" in jd
    assert "B.E/B.Tech" in detail["sections"]["Eligibility Criteria"]
    assert "Online assessment" in detail["sections"]["Hiring Workflow"]


def test_detail_panel_unknown_skills():
    detail = parse_detail_panel(
        "Opening Overview\nCategory: Tier 2\nRequired skills\nNo skills added for this job profile"
    )
    assert detail["skills"] == []