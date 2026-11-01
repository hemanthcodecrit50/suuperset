"""The agent graph: a bounded state machine from router to verified findings."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from app.agent import critic
from app.agent.approval import approval_gate
from app.agent.matcher import match
from app.agent.planner import plan_tasks
from app.agent.researcher import research
from app.agent.resume_advisor import advise_resume
from app.agent.router import route_change
from app.agent.state import AgentDeps, AgentState, Route
from app.agent.writer import write_report

logger = logging.getLogger(__name__)

# Eligibility fields are already reported by the matcher
FIT_COVERED_FIELDS = frozenset({"eligibility", "degree", "min_cgpa", "graduation_year"})

Node = Callable[[AgentState, AgentDeps], AgentState]
SPECIALISTS: tuple[tuple[str, Node], ...] = (
    ("researcher", research),
    ("matcher", match),
    ("resume_advisor", advise_resume),
)


def run_graph(state: AgentState, deps: AgentDeps) -> AgentState:
    """Route the item, then run the path it deserves; the critic retries at most once."""
    state = _timed("route", _route, state, deps)
    if state.route is Route.DROP:
        return state
    if state.route is Route.SHORT:
        state = _timed("match", match, state, deps)
        return _write_and_approve(state, deps)

    state = _timed("plan", _plan, state, deps)
    state = _timed("specialists", _run_specialists, state, deps)
    state = _timed("critic", critic.check_claims, state, deps)
    if critic.needs_retry(state, deps):
        state = _retry_research(state, deps)
    state = _timed("finalise", _finalise, state, deps)
    return _write_and_approve(state, deps)


def _write_and_approve(state: AgentState, deps: AgentDeps) -> AgentState:
    state = _timed("writer", write_report, state, deps)
    return _timed("approval", approval_gate, state, deps)


def _route(state: AgentState, deps: AgentDeps) -> AgentState:
    decision = route_change(
        state.opportunity, state.change_type, state.changes, state.memory
    )
    state.route = decision.route
    state.route_reason = decision.reason
    return state


def _plan(state: AgentState, deps: AgentDeps) -> AgentState:
    state.tasks = plan_tasks(state.opportunity, state.parsed_jd, state.memory)
    return state


def _run_specialists(state: AgentState, deps: AgentDeps) -> AgentState:
    """Run the researcher, matcher and resume advisor side by side."""
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(_timed, name, node, _private_copy(state), deps)
            for name, node in SPECIALISTS
        ]
        results = [future.result() for future in futures]
    researched, matched, advised = results
    for specialist in results:
        state.trace.extend(specialist.trace)
    state.evidence = researched.evidence
    state.claims = researched.claims
    state.fit = matched.fit
    state.resume_advice = advised.resume_advice
    state.unknowns.extend(matched.unknowns + advised.unknowns)
    return state


def _private_copy(state: AgentState) -> AgentState:
    # Each specialist writes its own fields, so give it its own copy of the state
    return AgentState(
        opportunity=state.opportunity,
        change_type=state.change_type,
        changes=state.changes,
        memory=state.memory,
        parsed_jd=state.parsed_jd,
        route=state.route,
        tasks=state.tasks,
        evidence=list(state.evidence),
        claims=list(state.claims),
        retry_count=state.retry_count,
        retry_topics=list(state.retry_topics),
    )


def _retry_research(state: AgentState, deps: AgentDeps) -> AgentState:
    state.retry_count += 1
    state.retry_topics = critic.failed_topics(state)
    logger.info("Critic retry for topics: %s", ", ".join(state.retry_topics))
    state = _timed("research_retry", research, state, deps)
    return _timed("critic_retry", critic.check_claims, state, deps)


def _finalise(state: AgentState, deps: AgentDeps) -> AgentState:
    state.unknowns.extend(_unknown_jd_fields(state))
    state = critic.drop_unsupported(state)
    if not state.claims:
        state.unknowns.append(
            "No verified public information about the company was found in free sources."
        )
    return state


def _unknown_jd_fields(state: AgentState) -> list[str]:
    return [
        f"JD does not state: {name}"
        for name in state.parsed_jd.get("unknown_fields") or []
        if name not in FIT_COVERED_FIELDS
    ]


def _timed(name: str, node: Node, state: AgentState, deps: AgentDeps) -> AgentState:
    started = time.monotonic()
    state = node(state, deps)
    seconds = time.monotonic() - started
    state.trace.append(f"{name}:{seconds:.2f}s")
    deps.record(name, describe_node(name, state), duration_ms=round(seconds * 1000))
    return state


def describe_node(name: str, state: AgentState) -> str:
    """One line saying what a node decided, for the Dev mode timeline."""
    if name == "route":
        return f"{state.route.value.upper()} · {state.route_reason}"
    if name == "plan":
        topics = ", ".join(task.topic for task in state.tasks) or "none"
        return f"{len(state.tasks)} lookups: {topics}"
    if name in ("researcher", "research_retry"):
        return (
            f"{len(state.evidence)} evidence items · {len(state.claims)} claims drafted"
        )
    if name == "matcher":
        return (
            f"fit {state.fit['score']} · {state.fit['eligibility']}"
            if state.fit
            else "no fit"
        )
    if name == "resume_advisor":
        advice = state.resume_advice
        if not advice:
            return "nothing to map"
        return f"{len(advice['lead_with'])} projects to lead with · {len(advice['gaps'])} gaps"
    if name in ("critic", "critic_retry") and state.verdict is not None:
        verdict = state.verdict
        supported = verdict.checked_count - verdict.unsupported_count
        return f"{supported}/{verdict.checked_count} claims supported"
    if name == "finalise":
        return (
            f"{len(state.claims)} verified claims kept · {len(state.unknowns)} unknowns"
        )
    if name == "writer":
        return "brief written"
    if name == "approval":
        return f"{state.pending_approvals} resume bullets waiting for approval"
    return "done"
