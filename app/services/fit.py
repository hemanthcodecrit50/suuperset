"""Score stored postings against the student's profile, and keep those scores current."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Opportunity
from app.services import jd_parser
from app.services.matching import MatchResult, score_opportunity
from app.services.preferences import load_preferences

ELIGIBILITY_FIELDS = ("degree", "min_cgpa", "graduation_year")


def jd_text_for(record: Opportunity) -> str:
    """Everything known about a posting's JD: portal fields, description and attached JD files."""
    parts = [f"Title: {record.title}", f"Company: {record.company_name or 'unknown'}"]
    labelled = (
        ("Location", record.location),
        ("Work Type", record.work_type),
        ("Compensation", record.compensation),
        ("Eligibility", record.eligibility),
    )
    parts += [f"{label}: {value}" for label, value in labelled if value]
    if record.skills:
        parts.append(
            "Required skills: " + ", ".join(str(skill) for skill in record.skills)
        )
    if record.deadline:
        parts.append(f"Application Deadline: {record.deadline.date().isoformat()}")
    if record.description:
        parts.append("\n" + record.description)
    for document in (record.raw or {}).get("jd_documents") or []:
        parts.append(f"\nAttached document: {document['name']}\n{document['text']}")
    return "\n".join(parts)


def fit_payload(record: Opportunity) -> dict[str, Any]:
    """Posting fields the scorer needs, parsing the JD once with rules if not done yet."""
    raw = record.raw or {}
    parsed = raw.get("parsed_jd")
    if not isinstance(parsed, dict):
        parsed = jd_parser.parse_jd(
            jd_text_for(record), title=record.title, company=record.company_name
        ).to_dict()
        record.raw = {**raw, "parsed_jd": parsed}
    payload: dict[str, Any] = {
        "title": record.title,
        "role": record.role,
        "job_function": record.job_function,
        "work_type": record.work_type,
        "location": record.location,
        "compensation": record.compensation,
        "description": record.description,
        "skills": record.skills or [],
        "eligibility": record.eligibility,
        "parsed_jd": parsed,
        "jd_insight": raw.get("jd_insight"),
    }
    for name in ELIGIBILITY_FIELDS:
        if parsed.get(name) is not None:
            payload[name] = parsed[name]
    return payload


def apply_fit(record: Opportunity, profile: dict[str, Any]) -> MatchResult:
    """Score one posting and store the result on it."""
    match = score_opportunity(fit_payload(record), profile)
    record.relevance_score = match.score
    record.relevance_reasons = match.reasons
    record.relevance_unknown = match.unknown
    record.eligible = match.hard_constraints_met
    return match


def rescore_all(session: Session) -> int:
    """Rescore every posting, e.g. after the profile changes. No AI is used."""
    profile = load_preferences(session).as_match_kwargs()
    records = list(session.scalars(select(Opportunity)))
    for record in records:
        apply_fit(record, profile)
    session.flush()
    return len(records)
