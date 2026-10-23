"""Opportunity relevance scoring and eligibility rules.

Scoring is intentionally simple and configurable. Eligibility requirements
(degree, CGPA, graduation year) are treated as *hard constraints* and evaluated
separately. Missing information is reported as ``unknown`` rather than assumed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping

logger = logging.getLogger(__name__)

# Configurable weights (as fractions). Override via user_preferences.
DEFAULT_WEIGHTS: dict[str, float] = {
    "skills_alignment": 0.35,
    "role_alignment": 0.25,
    "compensation_location": 0.20,
    "tech_worktype": 0.20,
}


@dataclass
class MatchResult:
    score: float
    breakdown: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    hard_constraints_met: bool | None = None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip().lower() for v in value.split(",") if v.strip()]
    return [str(v).strip().lower() for v in value]


def _overlap_score(have: list[str], want: list[str]) -> float:
    if not want:
        return 0.0
    hits = sum(1 for w in want if any(w in h or h in w for h in have))
    return min(1.0, hits / len(want))


def evaluate_eligibility(
    opportunity: Mapping[str, Any], profile: Mapping[str, Any]
) -> tuple[bool | None, list[str]]:
    """Evaluate hard constraints.

    Returns ``(met, unknown_fields)``. ``met`` is ``None`` when any required
    field is missing, because eligibility must never be assumed.
    """
    unknown: list[str] = []
    cgpa = opportunity.get("min_cgpa")
    grad_year = opportunity.get("graduation_year")
    degree = opportunity.get("degree")

    if cgpa is not None and profile.get("cgpa") is not None:
        if float(profile["cgpa"]) < float(cgpa):
            return False, unknown
    elif cgpa is not None:
        unknown.append("cgpa")

    if grad_year is not None and profile.get("graduation_year") is not None:
        if int(profile["graduation_year"]) != int(grad_year):
            return False, unknown
    elif grad_year is not None:
        unknown.append("graduation_year")

    if degree is not None and profile.get("degree") is not None:
        if str(degree).lower() not in str(profile["degree"]).lower():
            return False, unknown
    elif degree is not None:
        unknown.append("degree")

    if unknown:
        return None, unknown
    return True, unknown


def score_opportunity(
    opportunity: Mapping[str, Any],
    preferences: Mapping[str, Any] | None = None,
) -> MatchResult:
    """Score an opportunity against the user's preferences.

    ``preferences`` is a free-form mapping, typically assembled from the
    ``user_preferences`` table. Recognized keys include ``skills``,
    ``interests``, ``locations``, ``min_compensation``, ``technologies``,
    ``work_types`` and ``weights``.
    """
    preferences = preferences or {}
    weights = {**DEFAULT_WEIGHTS, **(preferences.get("weights") or {})}
    total_weight = sum(weights.values()) or 1.0

    skills = _as_list(preferences.get("skills"))
    interests = _as_list(preferences.get("interests"))
    technologies = _as_list(preferences.get("technologies"))
    work_types = _as_list(preferences.get("work_types"))
    locations = _as_list(preferences.get("locations"))

    opp_skills = _as_list(opportunity.get("skills"))
    opp_text = " ".join(
        str(opportunity.get(k) or "")
        for k in ("title", "role", "job_function", "description")
    ).lower()

    reasons: list[str] = []
    unknown: list[str] = []

    skills_alignment = _overlap_score(opp_skills or [opp_text], skills)
    if not skills:
        unknown.append("skills")
    if skills_alignment:
        reasons.append("Skills overlap with your profile")

    role_alignment = _overlap_score([opp_text], interests)
    if not interests:
        unknown.append("interests")

    tech_worktype = _overlap_score(opp_skills + [opp_text], technologies + work_types)
    if not (technologies or work_types):
        unknown.append("technologies/work_types")

    compensation_location = 0.0
    if locations:
        loc = str(opportunity.get("location") or "").lower()
        if loc and any(w in loc for w in locations):
            compensation_location += 0.5
        elif loc:
            reasons.append("Location may not match preferences")
    else:
        unknown.append("locations")
    if opportunity.get("compensation"):
        compensation_location += 0.5
        reasons.append("Compensation is listed")
    else:
        unknown.append("compensation")

    raw_scores = {
        "skills_alignment": skills_alignment * weights["skills_alignment"],
        "role_alignment": role_alignment * weights["role_alignment"],
        "compensation_location": compensation_location * weights["compensation_location"],
        "tech_worktype": tech_worktype * weights["tech_worktype"],
    }
    score = round(100.0 * sum(raw_scores.values()) / total_weight, 1)

    hard, hard_unknown = evaluate_eligibility(opportunity, preferences)
    unknown.extend(hard_unknown)

    return MatchResult(
        score=score,
        breakdown={k: round(v, 3) for k, v in raw_scores.items()},
        reasons=reasons,
        unknown=sorted(set(unknown)),
        hard_constraints_met=hard,
    )
