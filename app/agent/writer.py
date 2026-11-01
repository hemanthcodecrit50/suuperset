"""Writer node for the evidence-backed Critical Information Report."""

from __future__ import annotations

import logging
import re

from app.agent.brief import render_template
from app.agent.critic import numbers_in
from app.agent.state import AgentDeps, AgentState

logger = logging.getLogger(__name__)
URL_PATTERN = re.compile(r"https?://[^\s)\]>]+")

WRITER_SYSTEM = """You turn a verified draft into a short brief for a student deciding:
"Should I apply, and how do I prepare and present myself?"

Hard rules:
- Use ONLY facts from the draft and the JD facts. Never add facts, numbers, names or links.
- Keep every "([source](...))" link exactly as given, next to the fact it supports.
- Never show ids, internal score breakdowns or HTML tags.
- If the draft says eligibility or fit is unknown, say it is unknown. Never claim the
  student lacks or has something the draft does not state.
- Company snapshot holds only facts that carry a source link. If there are none, write
  "No verified public information found." Do not fill it with job details.
- Short sentences and plain bullets. No code fences, no title line.

Use exactly these Markdown sections in this order, and skip any with nothing to say:
### Verdict
Two or three sentences: lean apply, think carefully, or probably skip, and the deciding facts.
### Key facts
One bullet each for deadline, pay, location, eligibility and bond when known.
### Risk flags
### Company snapshot
### Likely rounds and prep
### Resume advice
### Unknown"""

JD_FACT_FIELDS = (
    "deadline",
    "pay",
    "location",
    "work_type",
    "eligibility",
    "bond",
    "rounds",
)


def write_report(state: AgentState, deps: AgentDeps) -> AgentState:
    """Polish the deterministic report with the LLM, or use its safe template."""
    fallback = render_template(state)
    if deps.llm is None or not deps.llm.configured:
        state.report_markdown = fallback
        return state

    writer_input = _writer_input(state, fallback)
    report = _complete_report(deps, writer_input)
    if not report or not _is_grounded(report, writer_input, state):
        logger.warning(
            "Writer returned no grounded report; using evidence-only template."
        )
        report = fallback
    state.report_markdown = report + ("" if report.endswith("\n") else "\n")
    return state


def _is_grounded(report: str, writer_input: str, state: AgentState) -> bool:
    # Every link must be a verified source and every number must come from the input
    allowed = {claim.source_url for claim in state.verified_claims}
    has_known_links = all(
        url.rstrip(".,") in allowed for url in URL_PATTERN.findall(report)
    )
    report_without_links = URL_PATTERN.sub(" ", report)
    return has_known_links and numbers_in(report_without_links) <= numbers_in(
        writer_input
    )


def _complete_report(deps: AgentDeps, prompt: str) -> str:
    """Use the text API when available, while supporting lightweight test fakes."""
    complete_text = getattr(deps.llm, "complete_text", None)
    if callable(complete_text):
        return str(complete_text(WRITER_SYSTEM, prompt, max_chars=12000) or "").strip()
    answer = deps.llm.complete_json(WRITER_SYSTEM, prompt)
    return str((answer or {}).get("report_markdown") or "").strip()


def writer(state: AgentState, deps: AgentDeps) -> AgentState:
    """Compatibility alias matching the graph node naming convention."""
    return write_report(state, deps)


def _writer_input(state: AgentState, draft: str) -> str:
    jd_facts = {
        name: state.parsed_jd.get(name)
        for name in JD_FACT_FIELDS
        if state.parsed_jd.get(name)
    }
    return f"DRAFT:\n{draft}\n\nJD FACTS:\n{jd_facts}"
