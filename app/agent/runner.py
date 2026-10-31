"""Entry point that runs the agent graph for one stored opportunity."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import partial
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
from app.services.llm import default_client

logger = logging.getLogger(__name__)


@dataclass
class BriefResult:
    state: AgentState | None
    markdown: str
    is_reused: bool = False


def default_deps(settings: Settings | None = None) -> AgentDeps:
    settings = settings or get_settings()
    context = ToolContext(session=None, settings=settings)
    return AgentDeps(
        settings=settings,
        call_tool=partial(_call_tool_in_context, context),
        llm=default_client(),
    )


def analyze_opportunity(
    session: Session,
    opportunity: Opportunity,
    change_type: str,
    changes: dict[str, Any] | None = None,
    *,
    deps: AgentDeps | None = None,
    should_save: bool = True,
) -> BriefResult:
    """Run the graph for one opportunity and return the rendered brief."""
    deps = deps or default_deps()
    session.flush()

    if should_save:
        stored = store.find_current_brief(
            session, opportunity.id, opportunity.content_hash
        )
        if stored is not None:
            return BriefResult(None, stored.content["markdown"], is_reused=True)

    state = _build_state(session, opportunity, change_type, changes or {}, deps)
    state = run_graph(state, deps)
    markdown = render_brief(state)

    if should_save and state.route is not Route.DROP:
        store.save_brief(session, state, markdown, opportunity.content_hash)
        store.save_company_evidence(session, state)
    return BriefResult(state, markdown)


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
    )


def _tool_result(
    context: ToolContext, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    response = call_tool(name, arguments, context)
    if not response["ok"]:
        raise RuntimeError(f"Tool {name} failed: {response['error']}")
    return response["result"]


def _call_tool_in_context(context: ToolContext, name: str, arguments: dict[str, Any]):
    return call_tool(name, arguments, context)


def _json_safe_changes(changes: dict[str, Any]) -> dict[str, Any]:
    return {
        field: {"old": _printable(pair.get("old")), "new": _printable(pair.get("new"))}
        for field, pair in changes.items()
    }


def _printable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool, list)):
        return value
    return str(value)
