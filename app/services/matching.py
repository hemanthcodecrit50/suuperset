"""Opportunity fit scoring and eligibility rules.

The fit score answers "how well do I match this job?" from the student's side:
how much of the JD's skill list they cover, whether the role suits their skills,
and whether location and pay fit. It is optimistic by design: information that
is missing counts as neutral, never as a mismatch. Eligibility (degree, CGPA,
graduation year) is a separate hard check, and unknown is never treated as no.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.services.jd_parser import find_technologies

logger = logging.getLogger(__name__)

# Configurable weights (as fractions). Override via user_preferences.
DEFAULT_WEIGHTS: dict[str, float] = {
    "skills_alignment": 0.35,
    "role_alignment": 0.25,
    "compensation_location": 0.20,
    "tech_worktype": 0.20,
}
# Score given to a part we cannot judge: optimistic, but below a confirmed match
NEUTRAL = 0.7
# Spellings that mean the same skill, matched against a whole skill name
SKILL_ALIASES = {
    "postgres": "postgresql",
    "js": "javascript",
    "ts": "typescript",
    "node": "node.js",
    "nodejs": "node.js",
    "express": "express.js",
    "ml": "machine learning",
    "ai": "artificial intelligence",
    "dsa": "data structures",
    "data structures and algorithms": "data structures",
    "oops": "object oriented programming",
    "oop": "object oriented programming",
    "golang": "go",
    "k8s": "kubernetes",
}
# Old and new city names, replaced wherever they appear in a location
PLACE_ALIASES = {
    "bangalore": "bengaluru",
    "bombay": "mumbai",
    "gurgaon": "gurugram",
    "madras": "chennai",
    "calcutta": "kolkata",
    "trivandrum": "thiruvananthapuram",
}
# Preferences that mean "any location in the country is fine"
ANYWHERE_PREFERENCES = ("india", "anywhere", "any", "pan india", "remote", "open")
ANYWHERE_LOCATIONS = (
    "pan india",
    "anywhere",
    "remote",
    "multiple locations",
    "all india",
)
ROLE_DOMAINS: dict[str, tuple[str, ...]] = {
    "software": (
        "software",
        "sde",
        "developer",
        "development",
        "backend",
        "frontend",
        "full stack",
        "fullstack",
        "web",
        "application",
        "cloud",
        "devops",
        "sre",
        "platform",
        "programmer",
        "system software",
        "mobile",
        "android",
        "ios",
        "security",
        "cyber",
        "qa",
        "test engineer",
    ),
    "data": (
        "data",
        "analyst",
        "analytics",
        "machine learning",
        "ml",
        "ai",
        "artificial intelligence",
        "scientist",
        "deep learning",
        "nlp",
        "computer vision",
        "business intelligence",
    ),
    "hardware": (
        "hardware",
        "embedded",
        "vlsi",
        "firmware",
        "electronics",
        "circuit",
        "asic",
        "fpga",
        "semiconductor",
        "rtl",
        "verification",
    ),
    "core": (
        "mechanical",
        "civil",
        "structures",
        "manufacturing",
        "aerospace",
        "chemical",
    ),
    "business": (
        "sales",
        "marketing",
        "business development",
        "hr ",
        "finance",
        "operations",
    ),
}
DOMAIN_SKILLS: dict[str, tuple[str, ...]] = {
    "software": (
        "python",
        "java",
        "javascript",
        "typescript",
        "c++",
        "c#",
        "go",
        "kotlin",
        "react",
        "node.js",
        "sql",
        "data structures",
        "algorithms",
        "docker",
        "git",
        "linux",
        "rest",
    ),
    "data": (
        "machine learning",
        "deep learning",
        "tensorflow",
        "pytorch",
        "scikit-learn",
        "pandas",
        "numpy",
        "sql",
        "statistics",
        "r",
        "data analysis",
        "artificial intelligence",
    ),
    "hardware": (
        "verilog",
        "systemverilog",
        "vhdl",
        "embedded c",
        "embedded",
        "vlsi",
        "microcontroller",
        "arduino",
        "raspberry pi",
        "fpga",
        "pcb design",
        "ltspice",
        "labview",
    ),
    "core": (
        "autocad",
        "solidworks",
        "ansys",
        "catia",
        "creo",
        "fusion 360",
        "simulink",
        "revit",
        "staad pro",
        "etabs",
        "plc",
        "thermodynamics",
        "fluid mechanics",
        "manufacturing",
    ),
    "business": (
        "excel",
        "tableau",
        "power bi",
        "salesforce",
        "google analytics",
        "seo",
        "tally",
        "marketing",
        "sales",
        "finance",
        "accounting",
        "communication",
    ),
}
DEGREE_FAMILIES = {
    "bachelor_engineering": (
        "btech",
        "be",
        "bachelorofengineering",
        "bacheloroftechnology",
    ),
    "master_engineering": ("mtech", "me", "masterofengineering", "masteroftechnology"),
    "bachelor_science": ("bsc", "bachelorofscience"),
    "master_science": ("msc", "masterofscience"),
    "bca": ("bca", "bachelorofcomputerapplications"),
    "mca": ("mca", "masterofcomputerapplications"),
    "mba": ("mba", "masterofbusinessadministration"),
    "phd": ("phd", "doctorofphilosophy"),
}
MAX_MISSING_SHOWN = 3
MIN_PHRASE_LENGTH = 4
# Words that say nothing about the field of a role on their own
GENERIC_ROLE_WORDS = frozenset(
    {
        "engineer",
        "engineering",
        "developer",
        "development",
        "intern",
        "internship",
        "trainee",
        "associate",
        "graduate",
        "junior",
        "senior",
        "analyst",
        "specialist",
        "executive",
        "role",
        "roles",
        "and",
        "or",
        "the",
        "of",
        "for",
        "in",
    }
)
MAX_PLAUSIBLE_CGPA = 10.0


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


def evaluate_eligibility(
    opportunity: Mapping[str, Any], profile: Mapping[str, Any]
) -> tuple[bool | None, list[str]]:
    """Evaluate hard constraints.

    Superset checks eligibility for the signed-in student itself; when it gives a
    verdict, that verdict wins. Otherwise the rules read from the JD are applied.
    Returns ``(met, unknown_fields)``; ``met`` is ``None`` when something is unknown.
    """
    portal_verdict = str(opportunity.get("eligibility") or "").strip().lower()
    if portal_verdict == "eligible":
        return True, []
    if portal_verdict == "not eligible":
        return False, []
    unknown: list[str] = []
    cgpa = opportunity.get("min_cgpa")
    if cgpa is not None and float(cgpa) >= MAX_PLAUSIBLE_CGPA:
        cgpa = (
            None  # A cut-off of 10 or more on a 10-point scale is a misread, not a rule
        )
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
        same = _same_degree(str(degree), str(profile["degree"]))
        if same is False:
            return False, unknown
        if same is None:
            unknown.append("degree")
    elif degree is not None:
        unknown.append("degree")

    if unknown:
        return None, unknown
    return True, unknown


def _same_degree(required: str, held: str) -> bool | None:
    # B.E., B.Tech and "Bachelor of Engineering" are the same family; unknown is not a no
    required_family = _degree_family(required)
    held_family = _degree_family(held)
    if required_family is None or held_family is None:
        return None
    return required_family == held_family


def _degree_family(degree: str) -> str | None:
    squashed = re.sub(r"[^a-z]", "", degree.lower())
    for family, spellings in DEGREE_FAMILIES.items():
        if any(squashed.startswith(spelling) for spelling in spellings):
            return family
    return None


def score_opportunity(
    opportunity: Mapping[str, Any],
    preferences: Mapping[str, Any] | None = None,
) -> MatchResult:
    """Score how well the student fits an opportunity, 0 to 100.

    When the AI has read the profile and the JD (``insight`` and ``jd_insight``),
    their clean skill lists and role words are used; otherwise the rules are.
    """
    preferences = preferences or {}
    weights = {**DEFAULT_WEIGHTS, **(preferences.get("weights") or {})}
    total_weight = sum(weights.values()) or 1.0
    insight = preferences.get("insight") or {}
    have = _with_parts(
        _normalise_all(
            _skill_list(preferences.get("skills"))
            + _skill_list(preferences.get("technologies"))
        )
    )
    implied = _implied_skills(insight)
    reasons: list[str] = []
    unknown: list[str] = []

    skills_part = _skills_part(opportunity, have, implied, reasons, unknown)
    role_part = _role_part(opportunity, have, insight, reasons)
    place_part = _place_and_pay_part(
        opportunity, preferences, insight, reasons, unknown
    )
    worktype_part = _worktype_part(opportunity, preferences, unknown)
    parts = {
        "skills_alignment": skills_part,
        "role_alignment": role_part,
        "compensation_location": place_part,
        "tech_worktype": worktype_part,
    }
    raw_scores = {name: value * weights[name] for name, value in parts.items()}
    score = round(100.0 * sum(raw_scores.values()) / total_weight, 1)

    hard, hard_unknown = evaluate_eligibility(opportunity, preferences)
    unknown.extend(hard_unknown)
    return MatchResult(
        score=score,
        breakdown={name: round(value, 3) for name, value in raw_scores.items()},
        reasons=reasons,
        unknown=sorted(set(unknown)),
        hard_constraints_met=hard,
    )


def _implied_skills(insight: Mapping[str, Any]) -> dict[str, str]:
    # Maps a skill the student implicitly has to the listed skill it comes from
    implied: dict[str, str] = {}
    for listed, extras in (insight.get("implied_skills") or {}).items():
        for extra in extras:
            implied.setdefault(_normalise(extra), listed)
    return implied


def _skills_part(
    opportunity: Mapping[str, Any],
    have: list[str],
    implied: dict[str, str],
    reasons: list[str],
    unknown: list[str],
) -> float:
    if not have:
        unknown.append("skills")
        return NEUTRAL
    required = _jd_skills(opportunity, have)
    if not required:
        unknown.append("jd_skills")
        return NEUTRAL
    direct = [skill for skill in required if _covers(skill, have)]
    via = {
        skill: implied[alternative]
        for skill in required
        if skill not in direct
        for alternative in _alternatives(skill)
        if alternative in implied
    }
    covered = len(direct) + len(via)
    missing = [skill for skill in required if skill not in direct and skill not in via]
    reasons.append(f"Covers {covered} of {len(required)} JD skills")
    if via:
        reasons.append(
            "Matched via "
            + ", ".join(f"{listed} → {skill}" for skill, listed in via.items())
        )
    if missing:
        reasons.append("Missing: " + ", ".join(missing[:MAX_MISSING_SHOWN]))
    return covered / len(required)


def _jd_skills(opportunity: Mapping[str, Any], have: list[str]) -> list[str]:
    jd_insight = _mapping(opportunity.get("jd_insight"))
    if jd_insight.get("required_skills"):
        return list(
            dict.fromkeys(_normalise_all(_skill_list(jd_insight["required_skills"])))
        )
    parsed = _mapping(opportunity.get("parsed_jd"))
    listed = _skill_list(opportunity.get("skills")) + _skill_list(parsed.get("skills"))
    if not listed:
        text = " ".join(
            str(opportunity.get(key) or "") for key in ("title", "description")
        )
        listed = _skill_list(find_technologies(text))
    # Prose the JD parser mistook for skills ("adaptability across") must not count against the student
    return [
        skill
        for skill in dict.fromkeys(_normalise_all(listed))
        if find_technologies(skill) or _covers(skill, have)
    ]


def _has_skill(skill: str, have: list[str]) -> bool:
    # "data structures" covers "data structures and algorithms", but "java" is not "javascript"
    return any(
        skill == owned
        or _contains_phrase(owned, skill)
        or _contains_phrase(skill, owned)
        for owned in have
    )


def _contains_phrase(text: str, phrase: str) -> bool:
    if len(phrase) < MIN_PHRASE_LENGTH:
        return False
    return (
        re.search(rf"(?<![a-z0-9+#]){re.escape(phrase)}(?![a-z0-9+#])", text)
        is not None
    )


def _skill_list(value: Any) -> list[str]:
    return [item for item in _as_list(value) if item]


def _alternatives(skill: str) -> list[str]:
    # "AI/ML" is met by either part; "TCP/IP" by the whole name
    parts = [_normalise(part) for part in skill.split("/") if part.strip()]
    return list(dict.fromkeys([skill, *parts]))


def _covers(requirement: str, have: list[str]) -> bool:
    return any(
        _has_skill(alternative, have) for alternative in _alternatives(requirement)
    )


def _with_parts(skills: list[str]) -> list[str]:
    # A student's "C/C++" counts as C, as C++ and as the combined name
    return list(
        dict.fromkeys(part for skill in skills for part in _alternatives(skill))
    )


def _role_part(
    opportunity: Mapping[str, Any],
    have: list[str],
    insight: Mapping[str, Any],
    reasons: list[str],
) -> float:
    jd_insight = _mapping(opportunity.get("jd_insight"))
    role_text = " ".join(
        [str(opportunity.get(key) or "") for key in ("title", "role", "job_function")]
        + [str(jd_insight.get("role_area") or "")]
        + [str(word) for word in jd_insight.get("role_keywords") or []]
    ).lower()
    if any(_role_matches(role, role_text) for role in insight.get("role_fits") or []):
        reasons.append("Role suits your profile")
        return 1.0
    if any(
        _role_matches(role, role_text) for role in insight.get("role_misfits") or []
    ):
        reasons.append("Role is outside your usual area")
        return 0.3
    return _role_part_by_rules(role_text, have, reasons)


def _role_matches(role: str, role_text: str) -> bool:
    # "backend developer" matches "Backend Engineer Intern"; generic words alone never match
    words = [
        word
        for word in re.findall(r"[a-z0-9+#]+", role.lower())
        if word not in GENERIC_ROLE_WORDS
    ]
    return bool(words) and any(
        _contains_phrase(role_text, word) or word == role_text for word in words
    )


def _role_part_by_rules(role_text: str, have: list[str], reasons: list[str]) -> float:
    role_domains = {
        domain
        for domain, words in ROLE_DOMAINS.items()
        if _mentions_any(role_text, words)
    }
    if not role_domains:
        return NEUTRAL
    student_domains = {
        domain
        for domain, skills in DOMAIN_SKILLS.items()
        if sum(skill in have for skill in skills) >= 2
    }
    if not student_domains:
        return NEUTRAL
    if role_domains & student_domains:
        reasons.append("Role suits your skills")
        return 1.0
    reasons.append("Role is outside your usual skill area")
    return 0.3


def _place_and_pay_part(
    opportunity: Mapping[str, Any],
    preferences: Mapping[str, Any],
    insight: Mapping[str, Any],
    reasons: list[str],
    unknown: list[str],
) -> float:
    location = _normalise_place(str(opportunity.get("location") or ""))
    wanted = [
        _normalise_place(place) for place in _as_list(preferences.get("locations"))
    ]
    # States and regions the AI expanded into cities ("Karnataka" -> Mysuru, Hubballi...)
    for cities in (insight.get("places") or {}).values():
        wanted += [_normalise_place(city) for city in cities]
    if not location or not wanted:
        place = NEUTRAL
        unknown.append("location" if not location else "locations")
    elif (
        insight.get("anywhere")
        or any(anywhere in location for anywhere in ANYWHERE_LOCATIONS)
        or any(place in ANYWHERE_PREFERENCES for place in wanted)
        or any(place in location for place in wanted if place)
    ):
        place = 1.0
        reasons.append("Location matches your preferences")
    else:
        place = 0.4
        reasons.append("Location may not match your preferences")
    if opportunity.get("compensation"):
        reasons.append("Pay is stated")
        return (place + 1.0) / 2
    unknown.append("compensation")
    return (place + NEUTRAL) / 2


def _worktype_part(
    opportunity: Mapping[str, Any], preferences: Mapping[str, Any], unknown: list[str]
) -> float:
    wanted = _as_list(preferences.get("work_types"))
    offered = str(opportunity.get("work_type") or "").lower()
    if not wanted or not offered:
        return NEUTRAL
    return 1.0 if any(kind in offered for kind in wanted) else 0.4


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mentions_any(text: str, words: tuple[str, ...]) -> bool:
    return any(
        re.search(rf"(?<![a-z]){re.escape(word.strip())}(?![a-z])", text)
        for word in words
    )


def _normalise_all(values: list[str]) -> list[str]:
    return [_normalise(value) for value in values if value.strip()]


def _normalise(value: str) -> str:
    text = " ".join(value.lower().split())
    return SKILL_ALIASES.get(text, text)


def _normalise_place(value: str) -> str:
    text = " ".join(value.lower().split())
    for alias, canonical in PLACE_ALIASES.items():
        text = re.sub(rf"(?<![a-z]){alias}(?![a-z])", canonical, text)
    return text
