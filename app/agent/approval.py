"""Human approval gate for evidence-backed resume bullet suggestions."""

from __future__ import annotations

from sqlalchemy import select

from app.agent.state import AgentDeps, AgentState
from app.models import ResumeSuggestion


def approval_gate(state: AgentState, deps: AgentDeps) -> AgentState:
    """Persist new bullets as pending and report how many need review."""
    state.pending_approvals = 0
    if deps.session is None or not state.resume_advice:
        return state

    existing = {
        (row.portfolio_item_id, row.requirement, row.bullet)
        for row in deps.session.scalars(
            select(ResumeSuggestion).where(
                ResumeSuggestion.opportunity_id == state.opportunity["id"]
            )
        )
    }
    projects = {item.get("id"): item for item in state.memory.portfolio}
    for rewrite in state.resume_advice.get("bullet_rewrites", []):
        project = projects.get(rewrite.get("project_id"), {})
        key = (
            rewrite.get("project_id"),
            rewrite.get("requirement"),
            rewrite.get("bullet"),
        )
        if key in existing:
            continue
        deps.session.add(
            ResumeSuggestion(
                opportunity_id=state.opportunity["id"],
                portfolio_item_id=rewrite.get("project_id"),
                requirement=rewrite.get("requirement"),
                bullet=rewrite["bullet"],
                evidence=project.get("evidence_url"),
                technologies=project.get("technologies") or [],
                approved=False,
            )
        )
        existing.add(key)
        state.pending_approvals += 1
    deps.session.flush()
    return state


def approve_bullets(state: AgentState, deps: AgentDeps) -> AgentState:
    """Alias retained for callers that describe the gate as an approval step."""
    return approval_gate(state, deps)
