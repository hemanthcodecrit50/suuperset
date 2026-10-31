"""Matcher node: hard eligibility check and fit score for one posting."""

from __future__ import annotations

from typing import Any

from app.agent.state import AgentDeps, AgentState

PARSED_ELIGIBILITY_FIELDS = ("degree", "min_cgpa", "graduation_year")


def match(state: AgentState, deps: AgentDeps) -> AgentState:
    """Score fit with the stored preferences and explain eligibility."""
    opportunity = dict(state.opportunity)
    for field_name in PARSED_ELIGIBILITY_FIELDS:
        if state.parsed_jd.get(field_name) is not None:
            opportunity[field_name] = state.parsed_jd[field_name]

    response = deps.call_tool(
        "score_fit",
        {"opportunity": opportunity, "preferences": state.memory.match_profile},
    )
    if not response["ok"]:
        state.unknowns.append(f"Fit score unavailable: {response['error']}")
        return state

    fit = response["result"]
    fit["eligibility"] = _describe_eligibility(fit)
    state.fit = fit
    return state


def _describe_eligibility(fit: dict[str, Any]) -> str:
    met = fit["hard_constraints_met"]
    if met is True:
        return "eligible"
    if met is False:
        return "not eligible"
    return "unknown (missing: " + ", ".join(_missing_eligibility_fields(fit)) + ")"


def _missing_eligibility_fields(fit: dict[str, Any]) -> list[str]:
    known_fields = ("cgpa", "graduation_year", "degree")
    return [name for name in fit["unknown"] if name in known_fields] or ["profile data"]
