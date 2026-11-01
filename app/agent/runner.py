"""Entry point that runs the agent graph for one stored opportunity."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.agent import store
from app.agent.brief import render_brief
from app.agent.graph import run_graph
from app.agent.memory import load_memory
from app.agent.state import AgentDeps, AgentState, Route
from app.agent.tools import ToolContext, call_tool
from app.config import Settings, get_settings
from app.models import Opportunity
from app.services.events import EventLog
from app.services.llm import LlmClient, default_client

logger = logging.getLogger(__name__)

# Only these tools have small, readable arguments worth showing in Dev mode
LOGGED_ARGUMENT_TOOLS = ("web_search", "fetch_page")


@dataclass
class BriefResult:
    state: AgentState | None
    markdown: str
    is_reused: bool = False


def default_deps(settings: Settings | None = None) -> AgentDeps:
    settings = settings or get_settings()
    events = EventLog(Path(settings.events_file))
    llm = default_client()
    llm.events = events
    deps = AgentDeps(settings=settings, call_tool=call_tool, llm=llm, events=events)
    context = ToolContext(session=None, settings=settings)
    deps.call_tool = partial(_logged_tool_call, deps, context)
    return deps


def analyze_opportunity(
    session: Session,
    opportunity: Opportunity,
    change_type: str,
    changes: dict[str, Any] | None = None,
    *,
    deps: AgentDeps | None = None,
    should_save: bool = True,
    should_reuse: bool = True,
    run_id: str | None = None,
) -> BriefResult:
    """Run the graph for one opportunity and return the rendered brief."""
    deps = deps or default_deps()
    deps.run_id = run_id or f"agent-{opportunity.id}-{int(time.time())}"
    if isinstance(deps.llm, LlmClient):
        deps.llm.run_id = deps.run_id
    # Without a session the approval gate stores nothing, which --no-save expects
    deps.session = session if should_save else None
    session.flush()

    if should_save and should_reuse:
        stored = store.find_current_brief(
            session, opportunity.id, opportunity.content_hash
        )
        if stored is not None:
            return BriefResult(None, stored.content["markdown"], is_reused=True)

    started = time.monotonic()
    deps.record(
        "run_started",
        f"{opportunity.title} at {opportunity.company_name or 'unknown company'}",
        payload={"opportunity_id": opportunity.id},
    )
    state = _build_state(session, opportunity, change_type, changes or {}, deps)
    state = run_graph(state, deps)
    markdown = render_brief(state)
    deps.record(
        "run_finished",
        f"{state.route.value if state.route else 'no'} route · retry {state.retry_count}",
        duration_ms=round((time.monotonic() - started) * 1000),
    )

    if should_save and state.route is not Route.DROP:
        store.save_brief(session, state, markdown, opportunity.content_hash)
        store.save_company_evidence(session, state)
        _save_fit(opportunity, state)
    return BriefResult(state, markdown)


def _save_fit(opportunity: Opportunity, state: AgentState) -> None:
    # Keeps the inbox fit chip in step with the score shown in the brief
    if state.fit is None:
        return
    opportunity.relevance_score = state.fit["score"]
    opportunity.relevance_reasons = state.fit["reasons"]
    opportunity.relevance_unknown = state.fit["unknown"]
    opportunity.eligible = state.fit["hard_constraints_met"]


def _build_state(
    session: Session,
    opportunity: Opportunity,
    change_type: str,
    changes: dict[str, Any],
    deps: AgentDeps,
) -> AgentState:
    session_context = ToolContext(session=session, settings=deps.settings)
    payload = _tool_result(
        session_context,
        "get_opportunity",
        {
            "opportunity_id": opportunity.id,
            "include_versions": False,
        },
    )
    parsed_jd = _tool_result(
        session_context,
        "parse_jd",
        {
            "opportunity_id": opportunity.id,
            "persist": True,
        },
    )
    memory = load_memory(session, payload["company"] or "", deps.settings)
    return AgentState(
        opportunity=payload,
        change_type=change_type,
        changes=_json_safe_changes(changes),
        memory=memory,
        parsed_jd=parsed_jd,
        run_id=deps.run_id,
    )


def _tool_result(
    context: ToolContext, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    response = call_tool(name, arguments, context)
    if not response["ok"]:
        raise RuntimeError(f"Tool {name} failed: {response['error']}")
    return response["result"]


def _logged_tool_call(
    deps: AgentDeps, context: ToolContext, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    started = time.monotonic()
    response = call_tool(name, arguments, context)
    if deps.events is not None:
        deps.events.record(
            "tool",
            name,
            _describe_tool_call(name, arguments, response),
            run_id=deps.run_id,
            duration_ms=round((time.monotonic() - started) * 1000),
            payload={"arguments": arguments} if name in LOGGED_ARGUMENT_TOOLS else {},
        )
    return response


def _describe_tool_call(
    name: str, arguments: dict[str, Any], response: dict[str, Any]
) -> str:
    if not response["ok"]:
        return f"failed: {response['error']}"
    result = response["result"]
    if name == "web_search":
        return f'"{arguments.get("query")}" · {len(result["results"])} results'
    if name == "fetch_page":
        return f"{arguments.get('url')} · {result.get('chars', 0)} characters"
    if name == "score_fit":
        return f"fit score {result['score']}"
    if name == "rank_portfolio":
        return f"{len(result['ranked'])} projects ranked against {len(result['requirements'])} requirements"
    return "ok"


def _json_safe_changes(changes: dict[str, Any]) -> dict[str, Any]:
    return {
        field: {"old": _printable(pair.get("old")), "new": _printable(pair.get("new"))}
        for field, pair in changes.items()
    }


def _printable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool, list)):
        return value
    return str(value)
