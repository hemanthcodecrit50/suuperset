"""Shared state and settings passed between the nodes of the agent graph."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from app.config import Settings
from app.services.llm import LlmClient
from app.services.research import Evidence

ToolCaller = Callable[[str, dict[str, Any]], dict[str, Any]]

HIGH_CONFIDENCE_SOURCES = ("official", "reference")
MEDIUM_CONFIDENCE_SOURCES = ("news",)


class Route(str, Enum):
    FULL = "full"
    SHORT = "short"
    DROP = "drop"


@dataclass
class Task:
    topic: str
    query: str
    reason: str


@dataclass
class Claim:
    text: str
    source_url: str
    topic: str
    source_type: str = "web"
    is_verified: bool | None = None
    check_note: str | None = None

    @property
    def confidence(self) -> str:
        if self.source_type in HIGH_CONFIDENCE_SOURCES:
            return "high"
        if self.source_type in MEDIUM_CONFIDENCE_SOURCES:
            return "medium"
        return "low"


@dataclass
class StudentMemory:
    """What the agent already knows about the student before it reads the posting."""

    match_profile: dict[str, Any] = field(default_factory=dict)
    portfolio: list[dict[str, Any]] = field(default_factory=list)
    cached_evidence: list[Evidence] = field(default_factory=list)
    is_company_familiar: bool = False
    is_company_applied: bool = False


@dataclass
class CriticVerdict:
    checked_count: int = 0
    unsupported_count: int = 0
    page_checks_used: int = 0
    dropped_claims: list[Claim] = field(default_factory=list)
    dropped_bullets: list[str] = field(default_factory=list)

    @property
    def unsupported_ratio(self) -> float:
        if not self.checked_count:
            return 0.0
        return self.unsupported_count / self.checked_count


@dataclass
class AgentState:
    opportunity: dict[str, Any]
    change_type: str
    changes: dict[str, Any]
    memory: StudentMemory
    parsed_jd: dict[str, Any] = field(default_factory=dict)
    route: Route | None = None
    route_reason: str = ""
    tasks: list[Task] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    fit: dict[str, Any] | None = None
    resume_advice: dict[str, Any] | None = None
    verdict: CriticVerdict | None = None
    retry_count: int = 0
    retry_topics: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)

    @property
    def company(self) -> str:
        return str(self.opportunity.get("company") or "").strip()

    @property
    def verified_claims(self) -> list[Claim]:
        return [claim for claim in self.claims if claim.is_verified]

    @property
    def verified_evidence(self) -> list[Evidence]:
        cited_urls = {claim.source_url for claim in self.verified_claims}
        return [item for item in self.evidence if item.source_url in cited_urls]

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe snapshot stored with the brief."""
        return {
            "opportunity_id": self.opportunity.get("id"),
            "change_type": self.change_type,
            "route": self.route.value if self.route else None,
            "route_reason": self.route_reason,
            "tasks": [asdict(task) for task in self.tasks],
            "claims": [
                {**asdict(claim), "confidence": claim.confidence}
                for claim in self.verified_claims
            ],
            "dropped_claims": [asdict(claim) for claim in self.verdict.dropped_claims]
            if self.verdict
            else [],
            "fit": self.fit,
            "resume_advice": self.resume_advice,
            "retry_count": self.retry_count,
            "unknowns": self.unknowns,
            "trace": self.trace,
        }


@dataclass
class AgentDeps:
    """Everything the nodes call out to; tests replace these with fakes."""

    settings: Settings
    call_tool: ToolCaller
    llm: LlmClient | None = None
