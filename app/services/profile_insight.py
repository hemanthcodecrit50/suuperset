"""Ask the AI, once per profile change, what a student's profile really covers."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PortfolioItem
from app.services.llm import LlmClient
from app.services.preferences import (
    KEY_PROFILE_INSIGHT,
    load_preferences,
    load_setting,
    save_setting,
)

MAX_IMPLIED_PER_SKILL = 4
MAX_CITIES_PER_PLACE = 12
MAX_ROLES = 15
MAX_TEXT = 40

PROFILE_SYSTEM = """You help match a college student to job postings. Read their profile and
describe what it covers, so simple keyword matching works for any field of study.

Rules:
- implied_skills: for skills the student listed, the names a job posting would use that this
  skill clearly satisfies: the broader field it belongs to and standard equivalents
  (e.g. "Deep Learning" -> ["Machine Learning", "Artificial Intelligence"],
  "Spring Boot" -> ["Java", "Backend Development"], "Postgres" -> ["PostgreSQL", "SQL"],
  "SolidWorks" -> ["CAD", "3D Modelling"]).
  Only fields a skill is part of, never fields that merely use it: Python is not Machine
  Learning. No generic categories ("Programming Language", "Problem Solving"), no abbreviations
  of the same name, and leave a skill out when nothing useful applies.
- places: for each preferred location that is a state, region or country, the main cities in it.
  Leave cities out.
- anywhere: true only if the student's locations say they are open to any location.
- role_fits: short role names that suit this profile (e.g. "backend developer", "design engineer").
- role_misfits: short role names clearly outside this profile.
- Never add skills the student does not have.

Respond with a JSON object:
{"implied_skills": {"<listed skill>": ["..."]}, "places": {"<listed place>": ["..."]},
 "anywhere": false, "role_fits": ["..."], "role_misfits": ["..."]}"""


def profile_source(session: Session) -> dict[str, Any]:
    """The profile facts the AI reads: skills, places, degree and real project technologies."""
    matching = load_preferences(session).matching
    projects = [
        {"name": item.name, "technologies": item.technologies or []}
        for item in session.scalars(select(PortfolioItem))
        if item.status != "planned"
    ]
    return {
        "skills": matching.get("skills") or [],
        "technologies": matching.get("technologies") or [],
        "interests": matching.get("interests") or [],
        "locations": matching.get("locations") or [],
        "degree": matching.get("degree"),
        "projects": projects,
    }


def refresh_profile_insight(session: Session, llm: LlmClient) -> bool:
    """Re-read the profile with the AI if it changed. True when a new insight was saved."""
    source = profile_source(session)
    source_hash = _hash(source)
    stored = load_setting(session, KEY_PROFILE_INSIGHT)
    if stored.get("source_hash") == source_hash or not llm.configured:
        return False
    if not (source["skills"] or source["technologies"] or source["locations"]):
        return False
    answer = llm.complete_json(PROFILE_SYSTEM, json.dumps(source))
    if answer is None:
        return (
            False  # Keep the previous insight; scoring falls back to rules where needed
        )
    insight = clean_insight(answer, source)
    insight.update(
        source_hash=source_hash,
        generated_at=dt.datetime.now(dt.UTC).isoformat(),
        model=llm.settings.research_model,
        removed=stored.get("removed") or [],
    )
    save_setting(session, KEY_PROFILE_INSIGHT, insight)
    return True


def forget_profile_insight(session: Session) -> None:
    """Make the next refresh ask the AI again, keeping what the student removed."""
    stored = load_setting(session, KEY_PROFILE_INSIGHT)
    save_setting(session, KEY_PROFILE_INSIGHT, {**stored, "source_hash": None})


def remove_insight_item(session: Session, item: str) -> None:
    """Stop using one AI suggestion, e.g. "skill:Java" or "place:Mysuru"."""
    stored = load_setting(session, KEY_PROFILE_INSIGHT)
    removed = list(dict.fromkeys([*(stored.get("removed") or []), item]))
    save_setting(session, KEY_PROFILE_INSIGHT, {**stored, "removed": removed})


def clean_insight(answer: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """Keep only well-formed suggestions tied to what the student actually listed."""
    listed_skills = {
        _key(skill): skill for skill in source["skills"] + source["technologies"]
    }
    for project in source["projects"]:
        for technology in project["technologies"]:
            listed_skills.setdefault(_key(technology), technology)
    listed_places = {_key(place): place for place in source["locations"]}
    return {
        "implied_skills": _tied_lists(
            answer.get("implied_skills"), listed_skills, MAX_IMPLIED_PER_SKILL
        ),
        "places": _tied_lists(
            answer.get("places"), listed_places, MAX_CITIES_PER_PLACE
        ),
        "anywhere": answer.get("anywhere") is True,
        "role_fits": _short_texts(answer.get("role_fits"), MAX_ROLES),
        "role_misfits": _short_texts(answer.get("role_misfits"), MAX_ROLES),
    }


def _tied_lists(
    value: object, listed: dict[str, str], limit: int
) -> dict[str, list[str]]:
    # A suggestion must hang off something the student listed, never stand on its own
    cleaned: dict[str, list[str]] = {}
    if not isinstance(value, dict):
        return cleaned
    for key, items in value.items():
        owner = listed.get(_key(str(key)))
        if owner is None:
            continue
        extras = [
            text for text in _short_texts(items, limit) if _key(text) != _key(owner)
        ]
        if extras:
            cleaned[owner] = extras
    return cleaned


def _short_texts(value: object, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    texts = [str(item).strip() for item in value if isinstance(item, str | int)]
    return list(dict.fromkeys(text for text in texts if 0 < len(text) <= MAX_TEXT))[
        :limit
    ]


def _key(text: str) -> str:
    return " ".join(text.lower().split())


def _hash(source: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()[:16]
