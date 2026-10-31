"""Researcher node: gather sourced evidence and turn it into citable claims."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from app.agent.planner import BOND, INTERVIEW_PROCESS, PAY
from app.agent.state import AgentDeps, AgentState, Claim, Task
from app.services.research import Evidence

logger = logging.getLogger(__name__)

MAX_PARALLEL_SEARCHES = 3
MAX_EVIDENCE_ITEMS = 12
MAX_SYNTHESISED_CLAIMS = 8
RESULTS_PER_QUERY = 3
ANECDOTAL_TOPICS = frozenset({BOND, PAY, INTERVIEW_PROCESS})
SOURCE_TRUST_ORDER = ("official", "reference", "news", "web", "news_low", "anecdotal")

CLAIMS_SYSTEM = """You turn numbered evidence snippets into short factual claims.

Hard rules:
- Use ONLY the evidence given. Never add facts from memory.
- Every claim must name the evidence_id it comes from.
- Copy numbers, names and dates exactly as the evidence states them.
- If the evidence does not support a point, leave it out.

Respond with a JSON object:
{"claims": [{"evidence_id": 0, "topic": "same topic as the evidence", "text": "one sentence"}]}"""


def research(state: AgentState, deps: AgentDeps) -> AgentState:
    """Run the planned lookups in parallel, then build claims from the evidence."""
    tasks = _tasks_for_this_pass(state)
    state.evidence = _merge_evidence(
        state.memory.cached_evidence,
        state.evidence,
        _run_searches(tasks, state.company, deps),
    )
    is_strict = state.retry_count > 0
    state.claims = _build_claims(state, deps, is_strict=is_strict)
    return state


def _tasks_for_this_pass(state: AgentState) -> list[Task]:
    if state.retry_count == 0:
        return state.tasks
    # A retry widens the search for each topic the critic could not verify
    return [
        Task(
            topic,
            f"{state.company} {topic.replace('_', ' ')}",
            "Critic could not verify.",
        )
        for topic in state.retry_topics
    ]


def _run_searches(tasks: list[Task], company: str, deps: AgentDeps) -> list[Evidence]:
    if not tasks:
        return []
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_SEARCHES) as pool:
        results = list(pool.map(lambda task: _search_one(task, company, deps), tasks))
    return [item for evidence in results for item in evidence]


def _search_one(task: Task, company: str, deps: AgentDeps) -> list[Evidence]:
    response = deps.call_tool(
        "web_search",
        {
            "query": task.query,
            "limit": RESULTS_PER_QUERY,
            "max_results": RESULTS_PER_QUERY,
        },
    )
    if not response["ok"]:
        logger.info("Search for %r failed: %s", task.query, response["error"])
        return []
    return [
        _to_evidence(item, task.topic)
        for item in response["result"]["results"]
        if _mentions_company(item, company)
    ]


def _mentions_company(item: dict, company: str) -> bool:
    # Search engines return neighbours (parent groups, schools); keep only the company
    name = company.lower().split()[0] if company.strip() else ""
    searchable = " ".join(
        str(item.get(key) or "") for key in ("claim", "title", "excerpt", "source_url")
    )
    return name in searchable.lower()


def _to_evidence(item: dict, topic: str) -> Evidence:
    source_type = item["source_type"]
    if topic in ANECDOTAL_TOPICS and source_type != "official":
        source_type = "anecdotal"
    return Evidence(
        claim=item["claim"],
        source_url=item["source_url"],
        source_type=source_type,
        title=item.get("title"),
        published_at=item.get("published_at"),
        excerpt=item.get("excerpt"),
        topic=topic,
    )


def _merge_evidence(*groups: list[Evidence]) -> list[Evidence]:
    merged: dict[str, Evidence] = {}
    for group in groups:
        for item in group:
            merged.setdefault(item.source_url.rstrip("/").lower(), item)
    ranked = sorted(merged.values(), key=lambda item: _trust_rank(item.source_type))
    return ranked[:MAX_EVIDENCE_ITEMS]


def _trust_rank(source_type: str) -> int:
    if source_type in SOURCE_TRUST_ORDER:
        return SOURCE_TRUST_ORDER.index(source_type)
    return len(SOURCE_TRUST_ORDER)


def _build_claims(
    state: AgentState, deps: AgentDeps, *, is_strict: bool
) -> list[Claim]:
    if is_strict or deps.llm is None or not deps.llm.configured:
        return _extractive_claims(state.evidence)
    synthesised = _synthesised_claims(state.evidence, deps)
    return synthesised or _extractive_claims(state.evidence)


def _extractive_claims(evidence: list[Evidence]) -> list[Claim]:
    return [
        Claim(
            text=item.claim,
            source_url=item.source_url,
            topic=item.topic,
            source_type=item.source_type,
        )
        for item in evidence
    ]


def _synthesised_claims(evidence: list[Evidence], deps: AgentDeps) -> list[Claim]:
    if not evidence:
        return []
    numbered = [
        {
            "evidence_id": index,
            "topic": item.topic,
            "text": item.claim,
            "type": item.source_type,
        }
        for index, item in enumerate(evidence)
    ]
    answer = deps.llm.complete_json(CLAIMS_SYSTEM, str(numbered))
    if not answer:
        return []
    claims: list[Claim] = []
    for entry in (answer.get("claims") or [])[:MAX_SYNTHESISED_CLAIMS]:
        source = _evidence_for(entry, evidence)
        if source is None or not str(entry.get("text") or "").strip():
            continue
        # The URL always comes from the evidence row, never from the model
        claims.append(
            Claim(
                text=str(entry["text"]).strip(),
                source_url=source.source_url,
                topic=source.topic,
                source_type=source.source_type,
            )
        )
    return claims


def _evidence_for(entry: dict, evidence: list[Evidence]) -> Evidence | None:
    evidence_id = entry.get("evidence_id")
    if not isinstance(evidence_id, int) or not 0 <= evidence_id < len(evidence):
        return None
    return evidence[evidence_id]
