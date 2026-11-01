"""Ask the AI what a posting really requires; every skill it names must appear in the JD."""

from __future__ import annotations

import hashlib
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Opportunity
from app.services.fit import jd_text_for
from app.services.llm import LlmClient

MAX_JD_CHARS = 6000
MAX_SKILLS = 8
MAX_KEYWORDS = 8
MAX_TEXT = 40
LIVE_STATUSES = ("OPEN_FOR_APPLICATIONS", "IN_PROCESS")

JD_SYSTEM = """You read a campus job posting and list what it asks for, for keyword matching.

Rules:
- required_skills: the concrete skills, tools, languages or subjects the job itself needs, most
  important first, at most 8, as short names written the way the posting writes them
  (e.g. "Python", "AutoCAD", "Thermodynamics").
- Leave out: degree or branch names (CSE, ECE, B.Tech), eligibility rules, software needed only
  to take an online test (browsers, proctoring or exam apps), everyday software everyone uses
  (Windows, MS Office, Google Chrome) unless the job is about it, and soft phrases.
- preferred_skills: skills listed as good to have or a plus.
- role_area: two to four words naming the kind of role, e.g. "backend development".
- role_keywords: up to eight short words or phrases naming the role kind and field.
- Use only what the posting says.

Respond with a JSON object:
{"required_skills": ["..."], "preferred_skills": ["..."], "role_area": "...", "role_keywords": ["..."]}"""


def needs_jd_insight(record: Opportunity) -> bool:
    """True when the posting has no insight yet or its JD text changed since."""
    stored = (record.raw or {}).get("jd_insight") or {}
    return stored.get("text_hash") != _hash(jd_text_for(record))


def interpret_jd(record: Opportunity, llm: LlmClient) -> bool:
    """Store the AI's reading of one JD on the posting. False when the model gave no answer."""
    text = jd_text_for(record)
    answer = llm.complete_json(JD_SYSTEM, text[:MAX_JD_CHARS])
    if answer is None:
        return False
    insight = {
        "required_skills": _found_in(answer.get("required_skills"), text),
        "preferred_skills": _found_in(answer.get("preferred_skills"), text),
        "role_area": _short(answer.get("role_area")),
        "role_keywords": [
            keyword
            for keyword in (
                _short(item) for item in _as_list(answer.get("role_keywords"))
            )
            if keyword
        ][:MAX_KEYWORDS],
        "text_hash": _hash(text),
        "model": llm.settings.research_model,
    }
    record.raw = {**(record.raw or {}), "jd_insight": insight}
    return True


def postings_needing_insight(session: Session, limit: int) -> list[Opportunity]:
    """Postings to read next: live and applied ones first, then the newest."""
    records = [
        record
        for record in session.scalars(select(Opportunity))
        if needs_jd_insight(record)
    ]
    records.sort(key=_priority)
    return records[:limit]


def _priority(record: Opportunity) -> tuple[int, float]:
    raw = record.raw or {}
    is_live = raw.get("job_profile_status") in LIVE_STATUSES
    is_applied = raw.get("application_status") == "APPLIED"
    first_seen = record.first_seen_at.timestamp() if record.first_seen_at else 0.0
    return (0 if is_live else 1 if is_applied else 2, -first_seen)


def _found_in(values: object, text: str) -> list[str]:
    # A skill the posting never mentions is the model's guess, so it is dropped
    squashed_text = _squash(text)
    kept: list[str] = []
    for value in _as_list(values):
        skill = _short(value)
        if (
            skill
            and _squash(skill)
            and _squash(skill) in squashed_text
            and skill not in kept
        ):
            kept.append(skill)
    return kept[:MAX_SKILLS]


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def _short(value: object) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= MAX_TEXT else ""


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9+#]", "", text.lower())


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]
