"""Render the graph's verified findings as a markdown brief for the student."""

from __future__ import annotations

from app.agent.planner import BOND, PAY, RISK_NEWS
from app.agent.state import AgentState, Route

DECISION_QUESTION = "Should I apply, and how do I prepare and present myself?"


def render_brief(state: AgentState) -> str:
    """Markdown with fit, risks, cited company facts, prep and resume advice."""
    if state.report_markdown:
        return state.report_markdown
    return render_template(state)


def render_template(state: AgentState) -> str:
    """Render the deterministic evidence-only report used by the writer fallback."""
    if state.route is Route.DROP:
        return ""
    lines = ["## Agent brief", "", f"_{DECISION_QUESTION}_", ""]
    lines += _fit_section(state)
    if state.route is Route.FULL:
        lines += _risk_section(state)
        lines += _company_section(state)
        lines += _prep_section(state)
        lines += _resume_section(state)
        lines += _unknown_section(state)
    return "\n".join(lines).rstrip() + "\n"


def _fit_section(state: AgentState) -> list[str]:
    fit = state.fit
    if fit is None:
        return ["**Fit:** unavailable", ""]
    lines = [
        f"**Fit score:** {fit['score']}/100 · **Eligibility:** {fit['eligibility']}",
    ]
    lines += [f"- {reason}" for reason in fit["reasons"]]
    if state.route is Route.SHORT:
        lines.append(f"- Short path: {state.route_reason}")
    return lines + [""]


def _risk_section(state: AgentState) -> list[str]:
    flags = _jd_risk_flags(state)
    flags += [
        f"{claim.text} ({claim.confidence} confidence, {claim.source_url})"
        for claim in state.claims
        if claim.topic in (BOND, RISK_NEWS)
    ]
    if not flags:
        return []
    return ["### Risk flags", ""] + [f"- {flag}" for flag in flags] + [""]


def _jd_risk_flags(state: AgentState) -> list[str]:
    flags: list[str] = []
    bond = state.parsed_jd.get("bond") or {}
    if bond.get("applicable"):
        flags.append(f"Bond clause in the JD: {bond.get('raw')}")
    if not state.parsed_jd.get("pay") and not state.opportunity.get("compensation"):
        flags.append("Pay is not stated in the JD.")
    return flags


def _company_section(state: AgentState) -> list[str]:
    claims = [
        claim for claim in state.claims if claim.topic not in (BOND, RISK_NEWS, PAY)
    ]
    if not claims:
        return []
    lines = ["### Company snapshot", ""]
    lines += [f"- {claim.text} ([source]({claim.source_url}))" for claim in claims]
    return lines + [""]


def _prep_section(state: AgentState) -> list[str]:
    rounds = state.parsed_jd.get("rounds") or []
    anecdotal = [
        claim for claim in state.claims if claim.topic in (PAY, "interview_process")
    ]
    if not rounds and not anecdotal:
        return []
    lines = ["### Likely rounds and prep", ""]
    lines += [f"- {name}" for name in rounds]
    lines += [
        f"- Anecdotal: {claim.text} ([source]({claim.source_url}))"
        for claim in anecdotal
    ]
    return lines + [""]


def _resume_section(state: AgentState) -> list[str]:
    advice = state.resume_advice
    if not advice:
        return []
    lines = ["### Resume advice", "", f"_{advice['note']}_", ""]
    lines += [
        f"- Lead with **{row['name']}** (covers: {', '.join(row['matched_requirements'])})"
        for row in advice["lead_with"]
    ]
    lines += [f"- Gap: {gap['requirement']}. {gap['advice']}" for gap in advice["gaps"]]
    lines += [
        f"- Bullet idea for {rewrite['requirement']}: {rewrite['bullet']}"
        for rewrite in advice["bullet_rewrites"]
    ]
    return lines + [""]


def _unknown_section(state: AgentState) -> list[str]:
    if not state.unknowns:
        return []
    return ["### Unknown", ""] + [f"- {item}" for item in state.unknowns] + [""]
