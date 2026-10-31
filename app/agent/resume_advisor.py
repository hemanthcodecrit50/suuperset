"""Resume advisor node: map JD requirements to the student's real projects."""

from __future__ import annotations

import logging
from typing import Any

from app.agent.state import AgentDeps, AgentState
from app.services.llm import BULLET_SYSTEM
from app.services.resume import meaningful_tokens

logger = logging.getLogger(__name__)

PROJECTS_TO_LEAD_WITH = 3
BULLET_PROJECT_COUNT = 2
UNUSABLE_PROJECT_STATUS = "planned"
ADVICE_NOTE = "Reword and reorder only. Add a keyword to your resume only if you truly have the skill."


def advise_resume(state: AgentState, deps: AgentDeps) -> AgentState:
    """Rank real projects for this JD and list honest gaps; never invent experience."""
    requirements = _jd_requirements(state)
    projects = [
        item
        for item in state.memory.portfolio
        if item.get("status") != UNUSABLE_PROJECT_STATUS
    ]
    if not requirements:
        state.unknowns.append("Resume advice: the JD lists no skills to map.")
        return state
    if not projects:
        state.unknowns.append("Resume advice: no verified portfolio items on file.")

    ranked = _rank_projects(requirements, projects, deps)
    state.resume_advice = {
        "requirements": requirements,
        "lead_with": ranked["rows"][:PROJECTS_TO_LEAD_WITH],
        "reorder": [row["name"] for row in ranked["rows"]],
        "gaps": _classify_gaps(ranked["possible_gaps"], requirements, state),
        "bullet_rewrites": _draft_bullets(ranked["rows"], projects, deps),
        "note": ADVICE_NOTE,
    }
    return state


def _jd_requirements(state: AgentState) -> list[str]:
    skills = list(state.parsed_jd.get("skills") or []) + list(
        state.opportunity.get("skills") or []
    )
    unique: dict[str, str] = {}
    for skill in skills:
        unique.setdefault(str(skill).strip().lower(), str(skill).strip())
    return [skill for key, skill in unique.items() if key]


def _rank_projects(
    requirements: list[str], projects: list[dict], deps: AgentDeps
) -> dict:
    response = deps.call_tool(
        "rank_portfolio",
        {"requirements": requirements, "portfolio_items": projects, "limit": 20},
    )
    if not response["ok"]:
        logger.info("rank_portfolio failed: %s", response["error"])
        return {"rows": [], "possible_gaps": list(requirements)}
    result = response["result"]
    rows = [
        {
            "id": item["id"],
            "name": item["name"],
            "score": item["score"],
            "status": item.get("status"),
            "evidence_url": item.get("evidence_url"),
            "matched_requirements": _matched_requirements(requirements, item),
        }
        for item in result["ranked"]
    ]
    return {"rows": rows, "possible_gaps": result["possible_gaps"]}


def _matched_requirements(
    requirements: list[str], project: dict[str, Any]
) -> list[str]:
    project_tokens = meaningful_tokens(_project_text(project))
    return [req for req in requirements if meaningful_tokens(req) & project_tokens]


def _project_text(project: dict[str, Any]) -> str:
    return " ".join(
        [
            str(project.get("name") or ""),
            str(project.get("description") or ""),
            " ".join(project.get("technologies") or []),
            " ".join(project.get("achievements") or []),
        ]
    )


def _classify_gaps(
    gaps: list[str], requirements: list[str], state: AgentState
) -> list[dict[str, Any]]:
    original_spelling = {
        requirement.lower(): requirement for requirement in requirements
    }
    profile_text = " ".join(
        str(value).lower()
        for key in ("skills", "technologies")
        for value in state.memory.match_profile.get(key) or []
    )
    return [
        {
            "requirement": original_spelling.get(gap, gap),
            "in_profile": gap in profile_text,
            "advice": (
                "Listed in your profile but no project shows it; add project evidence."
                if gap in profile_text
                else "Nothing on file shows this skill; do not claim it."
            ),
        }
        for gap in gaps
    ]


def _draft_bullets(
    rows: list[dict], projects: list[dict], deps: AgentDeps
) -> list[dict]:
    if deps.llm is None or not deps.llm.configured:
        return []
    records = {project["id"]: project for project in projects}
    bullets: list[dict] = []
    for row in rows[:BULLET_PROJECT_COUNT]:
        if not row["matched_requirements"]:
            continue
        requirement = row["matched_requirements"][0]
        answer = deps.llm.complete_json(
            BULLET_SYSTEM,
            str({"requirement": requirement, "project_record": records[row["id"]]}),
        )
        bullet = str((answer or {}).get("bullet") or "").strip()
        if bullet:
            bullets.append(
                {"project_id": row["id"], "requirement": requirement, "bullet": bullet}
            )
    return bullets
