"""Resume intelligence: map JDs to verified portfolio items.

Never fabricate experience, metrics, technologies or achievements. Every
suggestion must be linked to a real portfolio item and the JD requirement it
addresses, and must be approved by the user before use.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from sqlalchemy import select

from app.models import PortfolioItem, ResumeSuggestion
from app.services.matching import _as_list

logger = logging.getLogger(__name__)


@dataclass
class Suggestion:
    requirement: str
    bullet: str
    portfolio_item_id: int | None
    evidence: str | None
    technologies: list[str] = field(default_factory=list)
    approved: bool = False


# Words too common to be meaningful evidence of a skill match.
_STOPWORDS = {
    "and", "the", "with", "for", "from", "that", "this", "your", "you", "our",
    "will", "have", "has", "are", "was", "were", "not", "but", "any", "all",
    "who", "them", "their", "they", "his", "her", "she", "him", "its", "into",
    "out", "about", "role", "roles", "job", "jobs", "work", "working", "team",
    "teams", "year", "years", "etc", "via", "per", "new", "good", "must",
    "should", "would", "could", "may", "can", "are", "well", "more", "most",
    "such", "them", "then", "than", "been", "being", "does", "doing", "done",
}


def _tokens(text: str) -> set[str]:
    """Meaningful lowercase tokens for overlap scoring."""
    return {
        token
        for token in re.findall(r"[a-z0-9+#.\-]{3,}", str(text).lower())
        if token not in _STOPWORDS
    }


def rank_portfolio_items(
    requirements: Sequence[str],
    portfolio_items: Sequence[Mapping[str, Any]],
) -> list[tuple[int, float]]:
    """Rank portfolio items by token overlap with JD requirements.

    Token-level rather than whole-string matching, because most postings publish
    prose ("Roles: Backend Developer Intern") instead of atomic skill tags.
    Returns ``[(portfolio_item_id, score), ...]`` best-first; a score is the
    fraction of requirement tokens evidenced by the project record.
    """
    requirement_tokens: set[str] = set()
    for requirement in requirements:
        requirement_tokens |= _tokens(requirement)
    if not requirement_tokens:
        return []

    ranked: list[tuple[int, float]] = []
    for item in portfolio_items:
        haystack = " ".join(
            [
                str(item.get("name") or ""),
                str(item.get("description") or ""),
                " ".join(_as_list(item.get("technologies"))),
                " ".join(_as_list(item.get("achievements"))),
            ]
        )
        item_tokens = _tokens(haystack)
        if not item_tokens:
            continue
        hits = requirement_tokens & item_tokens
        if hits:
            ranked.append((int(item["id"]), round(len(hits) / len(requirement_tokens), 4)))
    return sorted(ranked, key=lambda pair: pair[1], reverse=True)


def suggest_bullets(
    *,
    requirements: Sequence[str],
    portfolio_items: Sequence[Mapping[str, Any]],
    model_hook: Any = None,
) -> list[Suggestion]:
    """Propose evidence-backed resume bullets for a job.

    ``model_hook`` is an optional callable ``(requirement, item) -> str`` that
    drafts a bullet grounded in the item. Without it, suggestions are left for
    manual authoring so no wording is invented automatically.
    """
    suggestions: list[Suggestion] = []
    ranked = rank_portfolio_items(requirements, portfolio_items)
    by_id = {int(item["id"]): item for item in portfolio_items}

    for req in requirements:
        for item_id, _score in ranked[:3]:
            item = by_id[item_id]
            if model_hook is not None:
                bullet = model_hook(req, item)
            else:
                bullet = (
                    f"[draft] Relates your work on '{item.get('name')}' "
                    f"to the requirement: {req}"
                )
            suggestions.append(
                Suggestion(
                    requirement=req,
                    bullet=bullet,
                    portfolio_item_id=item_id,
                    evidence=item.get("evidence_url"),
                    technologies=_as_list(item.get("technologies")),
                )
            )
    return suggestions


def _user_keywords(records: list[dict], session=None) -> list[str]:
    """Keywords describing what the user actually has.

    Combines portfolio technologies with saved preferences so requirement
    extraction can spot relevant lines in prose JDs.
    """
    keywords: list[str] = []
    for record in records:
        for tech in record.get("technologies") or []:
            if tech and str(tech) not in keywords:
                keywords.append(str(tech))
    if session is not None:
        try:
            from app.services.preferences import load_preferences

            preferences = load_preferences(session)
            for field_name in ("skills", "technologies", "interests"):
                for value in preferences.matching.get(field_name) or []:
                    if value and str(value) not in keywords:
                        keywords.append(str(value))
        except Exception:  # noqa: BLE001 - keywords are an optimisation only
            pass
    return keywords


def suggest_for_opportunity(
    session,
    opportunity,
    *,
    use_model: bool = True,
    persist: bool = False,
) -> list[Suggestion]:
    """Return evidence-backed bullets for a stored opportunity.

    Each suggestion links to a real portfolio item and its evidence URL. When
    no model is available the bullet is an explicit draft placeholder, which
    makes it obvious that a human still has to write the final wording.
    """
    items = session.scalars(select(PortfolioItem)).all()
    records = [
        {
            "id": item.id,
            "name": item.name,
            "description": item.description,
            "technologies": item.technologies or [],
            "achievements": item.achievements or [],
            "evidence_url": item.evidence_url,
            "status": item.status,
        }
        for item in items
    ]

    keywords = _user_keywords(records, session)
    requirements = _requirements_from_opportunity(opportunity, keywords=keywords)
    suggestions = suggest_bullets(
        requirements=requirements,
        portfolio_items=records,
        model_hook=(_model_hook() if use_model else None),
    )

    if persist:
        session.query(ResumeSuggestion).filter(
            ResumeSuggestion.opportunity_id == opportunity.id
        ).delete()
        for suggestion in suggestions:
            session.add(
                ResumeSuggestion(
                    opportunity_id=opportunity.id,
                    portfolio_item_id=suggestion.portfolio_item_id,
                    requirement=suggestion.requirement,
                    bullet=suggestion.bullet,
                    evidence=suggestion.evidence,
                    technologies=suggestion.technologies,
                    approved=False,
                )
            )
        session.flush()

    return suggestions


def skill_gaps(
    requirements: Sequence[str], portfolio_items: Sequence[Mapping[str, Any]]
) -> list[str]:
    """Return requirements with no supporting portfolio evidence."""
    reqs = _as_list(list(requirements))
    have = " ".join(
        " ".join(_as_list(item.get("technologies"))) + " " + str(item.get("description") or "")
        for item in portfolio_items
    ).lower()
    return [r for r in reqs if not any(word in have for word in r.split())]


def gaps_for_opportunity(session, opportunity) -> list[str]:
    """Skill gaps for a stored opportunity (JD requirements with no evidence)."""
    items = session.scalars(select(PortfolioItem)).all()
    records = [
        {
            "id": item.id,
            "description": item.description,
            "technologies": item.technologies or [],
        }
        for item in items
    ]
    return skill_gaps(
        _requirements_from_opportunity(
            opportunity, keywords=_user_keywords(records, session)
        ),
        records,
    )


REQUIREMENT_MARKERS = (
    "required", "requirement", "must have", "must be", "good to have",
    "experience with", "experience in", "knowledge of", "familiar with",
    "proficiency", "proficient", "exposure to", "ability to", "responsible",
    "you will", "you have", "we are looking", "strong communication",
    "skill", "role:", "role -", "qualifications", "competencies",
)


def _requirements_from_opportunity(opportunity, keywords: Sequence[str] | None = None) -> list[str]:
    """Extract requirement phrases from a job's JD.

    Prefers explicit requirement lines. When the JD is prose (most postings are),
    it also keeps any line that mentions one of ``keywords`` — typically the
    user's own technologies and interests — so matching has something grounded
    to work with instead of giving up.
    """
    requirements: list[str] = []
    for skill in opportunity.skills or []:
        if skill:
            requirements.append(str(skill).strip())

    text = opportunity.description or ""

    keyword_list = [
        k.strip().lower()
        for k in (keywords or [])
        if k and len(str(k).strip()) >= 3
    ]

    for line in text.splitlines():
        cleaned = " ".join(line.split())
        lowered = cleaned.lower()
        if not (3 < len(cleaned) < 200):
            continue
        if any(marker in lowered for marker in REQUIREMENT_MARKERS):
            requirements.append(cleaned)
        elif any(keyword in lowered for keyword in keyword_list):
            requirements.append(cleaned)

    # Deduplicate, keep order.
    seen: set[str] = set()
    unique = []
    for item in requirements:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    if unique:
        return unique[:12]

    # No JD or skills published for this role. Fall back to the title and
    # metadata, which the portal always shows.
    parts = [
        opportunity.title,
        opportunity.work_type,
        opportunity.job_function,
        opportunity.company_name,
    ]
    joined = " ".join(str(p) for p in parts if p)
    for token in re.split(r"[/,|]|\s[-–]\s", joined):
        token = " ".join(token.split()).strip(" -–")
        if 2 < len(token) < 60 and token.lower() not in seen:
            seen.add(token.lower())
            unique.append(token)
    return unique[:8]


def _model_hook():
    """Return a callable that drafts one bullet grounded in a project."""
    from app.services.llm import BULLET_SYSTEM, default_client

    client = default_client()
    if not client.configured:
        return None

    def hook(requirement: str, item: dict) -> str:
        payload = {"requirement": requirement, "project_record": item}
        generated = client.complete_json(BULLET_SYSTEM, str(payload))
        if not generated:
            return f"[draft - no model available] Requirement: {requirement}"
        bullet = generated.get("bullet")
        if not bullet:
            return (
                f"[unsupported - project record does not evidence: {requirement}]"
            )
        return str(bullet)

    return hook
