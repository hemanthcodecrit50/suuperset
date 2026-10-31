"""Fakes and builders shared by the agent graph tests."""

from __future__ import annotations

from typing import Any

from app.agent.researcher import CLAIMS_SYSTEM
from app.agent.state import AgentDeps, AgentState, StudentMemory
from app.agent.tools import ToolContext, call_tool
from app.config import Settings
from app.services.llm import BULLET_SYSTEM
from app.services.research import Evidence

FAR_FUTURE = "2999-01-01T00:00:00+00:00"
LONG_PAST = "2000-01-01T00:00:00+00:00"

PORTFOLIO = [
    {
        "id": 1,
        "name": "Order Service",
        "description": "Backend REST service for orders built with Python and PostgreSQL",
        "technologies": ["Python", "PostgreSQL", "Docker"],
        "achievements": [],
        "evidence_url": "https://github.com/me/order-service",
        "status": "implemented",
    },
    {
        "id": 2,
        "name": "Dream Project",
        "description": "Rust compiler",
        "technologies": ["Rust"],
        "achievements": [],
        "evidence_url": None,
        "status": "planned",
    },
]


def make_settings(**overrides: Any) -> Settings:
    return Settings(
        database_url="sqlite://",
        telegram_bot_token="",
        telegram_chat_id="",
        search_backend="wikipedia",
        search_cache_dir=".cache/test-search",
        search_cache_ttl_hours=0,
        **overrides,
    )


def make_search_result(
    claim: str, url: str, source_type: str = "reference", excerpt: str | None = None
) -> dict[str, Any]:
    return {
        "title": claim[:30],
        "claim": claim,
        "source_url": url,
        "source_type": source_type,
        "published_at": None,
        "excerpt": excerpt,
    }


def make_evidence(claim: str, url: str, topic: str = "company_overview") -> Evidence:
    return Evidence(claim=claim, source_url=url, source_type="reference", topic=topic)


def make_opportunity(**overrides: Any) -> dict[str, Any]:
    opportunity = {
        "id": 1,
        "company": "Zensar",
        "title": "Backend Engineer Intern",
        "role": "Backend Engineer",
        "skills": ["Python", "SQL"],
        "compensation": "12 LPA",
        "location": "Pune",
        "deadline": FAR_FUTURE,
        "application_status": None,
        "eligible": None,
    }
    return {**opportunity, **overrides}


def make_memory(**overrides: Any) -> StudentMemory:
    memory = StudentMemory(
        match_profile={"skills": ["python"], "technologies": ["python"], "cgpa": 8.0},
        portfolio=[dict(item) for item in PORTFOLIO],
    )
    for name, value in overrides.items():
        setattr(memory, name, value)
    return memory


def make_state(
    change_type: str = "new",
    changes: dict | None = None,
    parsed_jd: dict | None = None,
    memory: StudentMemory | None = None,
    **opportunity_overrides: Any,
) -> AgentState:
    return AgentState(
        opportunity=make_opportunity(**opportunity_overrides),
        change_type=change_type,
        changes=changes or {},
        memory=memory or make_memory(),
        parsed_jd=parsed_jd if parsed_jd is not None else {"skills": ["Python", "SQL"]},
    )


class FakeTools:
    """Fakes the network tools and runs the pure tools for real."""

    def __init__(
        self,
        settings: Settings,
        search_results: dict[str, list[dict]] | None = None,
        default_results: list[dict] | None = None,
        pages: dict[str, str] | None = None,
    ) -> None:
        self.settings = settings
        self.search_results = search_results or {}
        self.default_results = default_results or []
        self.pages = pages or {}
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        if name == "web_search":
            results = self.search_results.get(arguments["query"], self.default_results)
            return {"ok": True, "tool": name, "result": {"results": results}}
        if name == "fetch_page":
            return self._fetch(arguments["url"])
        return call_tool(name, arguments, ToolContext(settings=self.settings))

    def _fetch(self, url: str) -> dict[str, Any]:
        if url not in self.pages:
            return {"ok": False, "tool": "fetch_page", "error": "domain not allowed"}
        return {"ok": True, "tool": "fetch_page", "result": {"text": self.pages[url]}}

    def tool_names(self) -> list[str]:
        return [name for name, _ in self.calls]


class FakeLlm:
    configured = True

    def __init__(
        self, claims: list[dict] | None = None, bullet: str | None = None
    ) -> None:
        self.claims = claims
        self.bullet = bullet

    def complete_json(self, system: str, user: str, timeout: float = 0) -> dict | None:
        if system == CLAIMS_SYSTEM:
            return {"claims": self.claims} if self.claims is not None else None
        if system == BULLET_SYSTEM:
            return {"bullet": self.bullet}
        return None


def make_deps(
    tools: FakeTools | None = None, llm: FakeLlm | None = None, **setting_overrides: Any
) -> AgentDeps:
    settings = make_settings(**setting_overrides)
    return AgentDeps(settings=settings, call_tool=tools or FakeTools(settings), llm=llm)
