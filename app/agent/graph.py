"""The agent graph: a bounded state machine from router to verified findings."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from app.agent import critic
from app.agent.matcher import match
from app.agent.planner import plan_tasks
from app.agent.researcher import research
from app.agent.resume_advisor import advise_resume
from app.agent.router import route_change
from app.agent.state import AgentDeps, AgentState, Route

logger = logging.getLogger(__name__)

# Eligibility fields are already reported by the matcher
FIT_COVERED_FIELDS = frozenset({"eligibility", "degree", "min_cgpa", "graduation_year"})

Node = Callable[[AgentState, AgentDeps], AgentState]


def run_graph(state: AgentState, deps: AgentDeps) -> AgentState:
    """Route the item, then run the path it deserves; the critic retries at most once."""
    state = _timed("route", _route, state, deps)
    if state.route is Route.DROP:
        return state
    if state.route is Route.SHORT:
        return _timed("match", match, state, deps)

    state = _timed("plan", _plan, state, deps)
    state = _timed("specialists", _run_specialists, state, deps)
    state = _timed("critic", critic.check_claims, state, deps)
    if critic.needs_retry(state, deps):
        state = _retry_research(state, deps)
    return _timed("finalise", _finalise, state, deps)


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
            pool.submit(_run_on_copy, node, state, deps)
            for node in (research, match, advise_resume)
        ]
        results = [future.result() for future in futures]
    researched, matched, advised = results
    state.evidence = researched.evidence
    state.claims = researched.claims
    state.fit = matched.fit
    state.resume_advice = advised.resume_advice
    state.unknowns.extend(matched.unknowns + advised.unknowns)
    return state


def _run_on_copy(node: Node, state: AgentState, deps: AgentDeps) -> AgentState:
    # Each specialist writes its own fields, so give it its own copy of the state
    private = AgentState(
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
    return node(private, deps)


def _retry_research(state: AgentState, deps: AgentDeps) -> AgentState:
    state.retry_count += 1
    state.retry_topics = critic.failed_topics(state)
    logger.info("Critic retry for topics: %s", ", ".join(state.retry_topics))
    state = _timed("research_retry", research, state, deps)
    return _timed("critic_retry", critic.check_claims, state, deps)


def _finalise(state: AgentState, deps: AgentDeps) -> AgentState:
    state.unknowns.extend(_unknown_jd_fields(state))
    return critic.drop_unsupported(state)


def _unknown_jd_fields(state: AgentState) -> list[str]:
    return [
        f"JD does not state: {name}"
        for name in state.parsed_jd.get("unknown_fields") or []
        if name not in FIT_COVERED_FIELDS
    ]


def _timed(name: str, node: Node, state: AgentState, deps: AgentDeps) -> AgentState:
    started = time.monotonic()
    state = node(state, deps)
    state.trace.append(f"{name}:{time.monotonic() - started:.2f}s")
    return state
