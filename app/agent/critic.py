"""Critic node: check every claim and resume bullet against its cited source."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.agent.state import AgentDeps, AgentState, Claim, CriticVerdict
from app.services.resume import meaningful_tokens

MIN_TOKEN_COVERAGE = 0.6
NUMBER_PATTERN = re.compile(r"\d[\d,]*(?:\.\d+)?")


@dataclass(frozen=True)
class Grounding:
    is_supported: bool
    note: str


def check_claims(state: AgentState, deps: AgentDeps) -> AgentState:
    """Verify claims and bullets; on the final pass remove whatever stays unsupported."""
    verdict = CriticVerdict()
    source_texts = {item.source_url: _evidence_text(item) for item in state.evidence}
    page_texts: dict[str, str] = {}

    for claim in state.claims:
        verdict.checked_count += 1
        grounding = _check_claim(claim, source_texts, page_texts, verdict, deps)
        claim.is_verified = grounding.is_supported
        claim.check_note = grounding.note
        if not grounding.is_supported:
            verdict.unsupported_count += 1

    _check_bullets(state, verdict)
    state.verdict = verdict
    return state


def needs_retry(state: AgentState, deps: AgentDeps) -> bool:
    """True when too many claims failed and the single retry is still unused."""
    if state.retry_count >= 1 or state.verdict is None:
        return False
    return state.verdict.unsupported_ratio > deps.settings.agent_max_unsupported_ratio


def drop_unsupported(state: AgentState) -> AgentState:
    """Remove claims that failed verification and record what is now unknown."""
    verdict = state.verdict
    verdict.dropped_claims = [claim for claim in state.claims if not claim.is_verified]
    state.claims = state.verified_claims
    for topic in sorted({claim.topic for claim in verdict.dropped_claims}):
        if not any(claim.topic == topic for claim in state.claims):
            state.unknowns.append(f"No verified source for: {topic.replace('_', ' ')}")
    return state


def failed_topics(state: AgentState) -> list[str]:
    return sorted({claim.topic for claim in state.claims if not claim.is_verified})


def is_text_supported(claim_text: str, source_text: str) -> Grounding:
    """Check numbers exactly and key words by coverage against a source text."""
    claim_numbers = numbers_in(claim_text)
    missing_numbers = claim_numbers - numbers_in(source_text)
    if missing_numbers:
        return Grounding(
            False, f"Source does not contain: {', '.join(sorted(missing_numbers))}"
        )

    claim_tokens = _normalised_tokens(claim_text)
    if not claim_tokens:
        return Grounding(False, "Claim has no checkable content.")
    coverage = len(claim_tokens & _normalised_tokens(source_text)) / len(claim_tokens)
    if coverage < MIN_TOKEN_COVERAGE:
        return Grounding(
            False, f"Only {coverage:.0%} of the claim appears in the source."
        )
    return Grounding(True, f"{coverage:.0%} of the claim appears in the source.")


def _check_claim(
    claim: Claim,
    source_texts: dict[str, str],
    page_texts: dict[str, str],
    verdict: CriticVerdict,
    deps: AgentDeps,
) -> Grounding:
    snippet = source_texts.get(claim.source_url)
    if snippet is None:
        return Grounding(False, "Cited source is not among the gathered evidence.")
    grounding = is_text_supported(claim.text, snippet)
    if grounding.is_supported:
        return grounding

    page_text = _fetch_page_text(claim.source_url, page_texts, verdict, deps)
    if page_text is None:
        return grounding
    return is_text_supported(claim.text, page_text)


def _fetch_page_text(
    url: str, page_texts: dict[str, str], verdict: CriticVerdict, deps: AgentDeps
) -> str | None:
    if url in page_texts:
        return page_texts[url]
    if verdict.page_checks_used >= deps.settings.agent_max_page_checks:
        return None
    verdict.page_checks_used += 1
    response = deps.call_tool("fetch_page", {"url": url})
    if not response["ok"]:
        return None
    page_texts[url] = response["result"]["text"]
    return page_texts[url]


def _check_bullets(state: AgentState, verdict: CriticVerdict) -> None:
    advice = state.resume_advice
    if not advice:
        return
    records = {project["id"]: project for project in state.memory.portfolio}
    kept: list[dict] = []
    for rewrite in advice["bullet_rewrites"]:
        record = records.get(rewrite["project_id"])
        if record is not None and _is_bullet_grounded(rewrite["bullet"], record):
            kept.append(rewrite)
        else:
            verdict.dropped_bullets.append(rewrite["bullet"])
    advice["bullet_rewrites"] = kept


def _is_bullet_grounded(bullet: str, project: dict) -> bool:
    record_text = " ".join(
        [
            str(project.get("name") or ""),
            str(project.get("description") or ""),
            " ".join(project.get("technologies") or []),
            " ".join(project.get("achievements") or []),
        ]
    )
    return is_text_supported(bullet, record_text).is_supported


def _evidence_text(item) -> str:
    return " ".join(part for part in (item.claim, item.excerpt, item.title) if part)


def numbers_in(text: str) -> set[str]:
    """Numbers in a text, normalised so 1,000 and 1000 compare equal."""
    return {
        match.replace(",", "").rstrip(".") for match in NUMBER_PATTERN.findall(text)
    }


def _normalised_tokens(text: str) -> set[str]:
    without_numbers = NUMBER_PATTERN.sub(" ", text)
    return {
        token.strip(".-").removesuffix("s")
        for token in meaningful_tokens(without_numbers)
        if token.strip(".-")
    }
