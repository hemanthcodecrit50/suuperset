"""Writer node for the evidence-backed Critical Information Report."""

from __future__ import annotations

import logging
import re

from app.agent.brief import render_template
from app.agent.state import AgentDeps, AgentState

logger = logging.getLogger(__name__)
URL_PATTERN = re.compile(r"https?://[^\s)\]>]+")

WRITER_SYSTEM = """Write a concise Critical Information Report in Markdown.
Use ONLY the verified claims and structured facts supplied by the user.
Do not add facts, recommendations, metrics, or technologies that are not
present in that input. Preserve source URLs for sourced claims. Return only
the Markdown report, with no code fence."""


def write_report(state: AgentState, deps: AgentDeps) -> AgentState:
    """Polish the deterministic report with the LLM, or use its safe template."""
    fallback = render_template(state)
    if deps.llm is None or not deps.llm.configured:
        state.report_markdown = fallback
        return state

    report = _complete_report(deps, _writer_input(state))
    if not report or not _uses_allowed_sources(report, state):
        logger.warning("Writer returned no grounded report; using evidence-only template.")
        report = fallback
    state.report_markdown = report + ("" if report.endswith("\n") else "\n")
    return state


def _uses_allowed_sources(report: str, state: AgentState) -> bool:
    allowed = {claim.source_url for claim in state.verified_claims}
    return all(url.rstrip(".,") in allowed for url in URL_PATTERN.findall(report))


def _complete_report(deps: AgentDeps, prompt: str) -> str:
    """Use the text API when available, while supporting lightweight test fakes."""
    complete_text = getattr(deps.llm, "complete_text", None)
    if callable(complete_text):
        return str(
            complete_text(WRITER_SYSTEM, prompt, max_chars=12000) or ""
        ).strip()
    answer = deps.llm.complete_json(WRITER_SYSTEM, prompt)
    return str((answer or {}).get("report_markdown") or "").strip()


def writer(state: AgentState, deps: AgentDeps) -> AgentState:
    """Compatibility alias matching the graph node naming convention."""
    return write_report(state, deps)


def _writer_input(state: AgentState) -> str:
    return str(
        {
            "fit": state.fit,
            "verified_claims": [
                {
                    "text": claim.text,
                    "source_url": claim.source_url,
                    "topic": claim.topic,
                    "confidence": claim.confidence,
                }
                for claim in state.verified_claims
            ],
            "resume_advice": state.resume_advice,
            "parsed_jd": state.parsed_jd,
            "unknowns": state.unknowns,
            "route_reason": state.route_reason,
        }
    )
