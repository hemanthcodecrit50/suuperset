"""Planner node: pick the lookups this particular posting needs."""

from __future__ import annotations

import re
from typing import Any

from app.agent.state import StudentMemory, Task

COMPANY_OVERVIEW = "company_overview"
OFFICIAL_SITE = "official_site"
RECENT_NEWS = "recent_news"
RISK_NEWS = "risk_news"
BOND = "bond"
PAY = "pay"
INTERVIEW_PROCESS = "interview_process"
ROLE_CONTEXT = "role_context"

MAX_TASKS = 5
LEGAL_SUFFIX_PATTERN = re.compile(
    r"\b(pvt|private|ltd|limited|llp|inc|corp|corporation|plc|gmbh)\b\.?", re.IGNORECASE
)
# Topics that stay true for every role at the company, so they are safe to cache
COMPANY_LEVEL_TOPICS = frozenset(
    {COMPANY_OVERVIEW, OFFICIAL_SITE, RECENT_NEWS, RISK_NEWS, BOND}
)


def search_name(company: str) -> str:
    """Company name without legal suffixes, which make search engines miss it."""
    cleaned = LEGAL_SUFFIX_PATTERN.sub(" ", company)
    return " ".join(cleaned.replace(",", " ").split()).strip(" .")


def plan_tasks(
    opportunity: dict[str, Any], parsed_jd: dict[str, Any], memory: StudentMemory
) -> list[Task]:
    """Choose lookups from what the JD says and what is already known."""
    company = search_name(str(opportunity.get("company") or ""))
    if not company:
        return []
    role = str(opportunity.get("role") or opportunity.get("title") or "").strip()
    is_bond_applicable = bool((parsed_jd.get("bond") or {}).get("applicable"))
    cached_topics = {item.topic for item in memory.cached_evidence}
    is_unfamiliar = not memory.is_company_familiar and not cached_topics

    tasks: list[Task] = []
    if COMPANY_OVERVIEW not in cached_topics:
        tasks.append(
            Task(COMPANY_OVERVIEW, company, "No stored brief for this company.")
        )
    if is_unfamiliar:
        tasks.append(
            Task(OFFICIAL_SITE, f"{company} official website", "Unfamiliar company.")
        )
        tasks.append(Task(RECENT_NEWS, f"{company} news", "Unfamiliar company."))
    if (is_unfamiliar or is_bond_applicable) and RISK_NEWS not in cached_topics:
        tasks.append(
            Task(
                RISK_NEWS, f"{company} layoffs controversy", "Check for negative news."
            )
        )
    if is_bond_applicable and BOND not in cached_topics:
        tasks.append(
            Task(
                BOND,
                f"{company} service agreement bond employees",
                "The JD has a bond clause.",
            )
        )
    if not parsed_jd.get("pay") and not opportunity.get("compensation"):
        tasks.append(
            Task(PAY, f"{company} {role} salary", "The JD does not state pay.")
        )
    if not parsed_jd.get("rounds"):
        tasks.append(
            Task(
                INTERVIEW_PROCESS,
                f"{company} interview process {role}",
                "No rounds in the JD.",
            )
        )
    if role:
        tasks.append(Task(ROLE_CONTEXT, f"{company} {role}", "Role-specific context."))
    return tasks[:MAX_TASKS]
