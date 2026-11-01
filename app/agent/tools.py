"""The agent's tool layer.

Every capability the week-2 graph needs is registered here with a JSON schema
and a single handler. Existing services are wrapped rather than reimplemented:

| Tool | Wraps |
| --- | --- |
| ``get_opportunity`` | opportunity + version history queries |
| ``parse_jd`` | the deterministic JD parser |
| ``web_search`` | the composite search backend |
| ``fetch_page`` | the allow-listed page fetcher |
| ``score_fit`` | ``services.matching`` |
| ``rank_portfolio`` | ``services.resume`` |
| ``send_alert`` | ``collector.change_detector.enqueue_alert`` |
| ``research_company`` | ``services.research`` |
| ``get_student_profile`` | preferences + portfolio |

Tools are read-only except ``parse_jd`` (only with ``persist``),
``score_fit`` (only with ``persist``) and ``send_alert`` (queue only). Nothing
writes to the Superset portal and no resume edit is ever published.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector import change_detector
from app.config import Settings, get_settings
from app.models import Application, Opportunity, OpportunityVersion, PortfolioItem
from app.services import (
    jd_parser,
    matching,
    pages,
    preferences,
    research,
    resume,
    search,
)
from app.services.applications import find_opportunity
from app.services.fit import jd_text_for

logger = logging.getLogger(__name__)


class ToolError(RuntimeError):
    """A tool could not complete; the message is safe to show to a model."""


@dataclass
class ToolContext:
    """Everything a tool may need. Session is optional for pure tools."""

    session: Session | None = None
    settings: Settings | None = None

    def get_settings(self) -> Settings:
        return self.settings or get_settings()

    def require_session(self) -> Session:
        if self.session is None:
            raise ToolError("This tool needs a database session.")
        return self.session


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[ToolContext, dict[str, Any]], dict[str, Any]]


# -- serialisation helpers ---------------------------------------------


def _iso(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.UTC)
    return value.isoformat()


def _opportunity_payload(record: Opportunity) -> dict[str, Any]:
    raw = record.raw if isinstance(record.raw, dict) else {}
    parsed = raw.get("parsed_jd") if isinstance(raw.get("parsed_jd"), dict) else {}
    return {
        "id": record.id,
        "external_id": record.external_id,
        "title": record.title,
        "company": record.company_name,
        "role": record.role,
        "location": record.location,
        "job_function": record.job_function,
        "work_type": record.work_type,
        "compensation": record.compensation,
        "eligibility": record.eligibility,
        "skills": record.skills or [],
        "deadline": _iso(record.deadline),
        "description": record.description,
        "apply_url": record.apply_url,
        "first_seen_at": _iso(record.first_seen_at),
        "last_seen_at": _iso(record.last_seen_at),
        "relevance_score": record.relevance_score,
        "relevance_reasons": record.relevance_reasons or [],
        "relevance_unknown": record.relevance_unknown or [],
        "eligible": record.eligible,
        "application_status": raw.get("application_status") or raw.get("job_profile_status"),
        "parsed_jd": parsed or None,
        "jd_insight": raw.get("jd_insight"),
    }


def _version_payload(version: OpportunityVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "content_hash": version.content_hash,
        "title": version.title,
        "description": version.description,
        "eligibility": version.eligibility,
        "deadline": _iso(version.deadline),
        "skills": version.skills or [],
        "captured_at": _iso(version.captured_at),
    }


def _version_changes(session: Session, record: Opportunity) -> dict[str, Any]:
    version = session.scalar(
        select(OpportunityVersion)
        .where(OpportunityVersion.opportunity_id == record.id)
        .order_by(OpportunityVersion.id.desc())
        .limit(1)
    )
    if version is None:
        return {}
    old = {
        "title": version.title,
        "description": version.description,
        "eligibility": version.eligibility,
        "deadline": version.deadline,
        "skills": version.skills,
    }
    new = {field: getattr(record, field, None) for field in old}
    return change_detector.diff_fields(old, new, tuple(old))


def _application_payload(session: Session, record: Opportunity) -> dict[str, Any] | None:
    application = session.scalar(
        select(Application).where(Application.opportunity_id == record.id)
    )
    if application is None:
        return None
    return {
        "status": application.status,
        "priority": application.priority,
        "next_action": application.next_action,
        "next_action_at": _iso(application.next_action_at),
        "notes": application.notes,
        "applied_at": _iso(application.applied_at),
    }


def _evidence_payload(item: research.Evidence) -> dict[str, Any]:
    return {
        "title": item.title,
        "claim": item.claim,
        "source_url": item.source_url,
        "source_type": item.source_type,
        "published_at": item.published_at,
        "excerpt": item.excerpt,
    }


def _default_channel(settings: Settings) -> str:
    if settings.telegram_configured:
        return "telegram"
    if settings.email_configured:
        return "email"
    return "dashboard"


def _requirements_for(
    session: Session | None, opportunity: Opportunity | None
) -> list[str]:
    if opportunity is None:
        return []
    records: list[dict[str, Any]] = []
    if session is not None:
        records = [
            {
                "technologies": item.technologies or [],
                "description": item.description or "",
            }
            for item in session.scalars(select(PortfolioItem)).all()
        ]
    keywords = resume._user_keywords(records, session) if session is not None else []
    return resume._requirements_from_opportunity(opportunity, keywords=keywords)


# -- tool handlers ------------------------------------------------------


def _tool_get_opportunity(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    session = ctx.require_session()
    record = find_opportunity(
        session,
        str(args.get("query") or ""),
        args.get("opportunity_id"),
    )
    if record is None:
        raise ToolError("No opportunity matched that id or query.")

    payload = _opportunity_payload(record)
    payload["application"] = _application_payload(session, record)

    if args.get("include_versions", True):
        versions = session.scalars(
            select(OpportunityVersion)
            .where(OpportunityVersion.opportunity_id == record.id)
            .order_by(OpportunityVersion.id.desc())
            .limit(5)
        ).all()
        payload["versions"] = [_version_payload(version) for version in versions]
        payload["changes"] = _version_changes(session, record)
    return payload


def _tool_parse_jd(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    text = args.get("text")
    opportunity = None
    opportunity_id = args.get("opportunity_id")

    if not text:
        if opportunity_id is None:
            raise ToolError("Provide jd text or an opportunity_id with a stored JD.")
        session = ctx.require_session()
        opportunity = session.get(Opportunity, int(opportunity_id))
        if opportunity is None:
            raise ToolError(f"No opportunity with id {opportunity_id}.")
        text = jd_text_for(opportunity)

    parsed = jd_parser.parse_jd(
        str(text),
        title=args.get("title") or (opportunity.title if opportunity else None),
        company=args.get("company") or (opportunity.company_name if opportunity else None),
    )
    result = parsed.to_dict()
    result["source"] = {
        "opportunity_id": opportunity.id if opportunity else opportunity_id,
        "text_chars": len(str(text)),
    }

    if args.get("persist"):
        if opportunity is None:
            if opportunity_id is None:
                raise ToolError("persist requires an opportunity_id (or stored JD).")
            session = ctx.require_session()
            opportunity = session.get(Opportunity, int(opportunity_id))
            if opportunity is None:
                raise ToolError(f"No opportunity with id {opportunity_id}.")
        session = ctx.require_session()
        raw = dict(opportunity.raw or {})
        raw["parsed_jd"] = parsed.to_dict()
        raw["parsed_jd_at"] = dt.datetime.now(dt.UTC).isoformat()
        opportunity.raw = raw
        session.flush()

    return result


def _tool_web_search(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    company = str(args.get("company") or "").strip()
    role = str(args.get("role") or "").strip()

    queries: list[str] = []
    if query:
        queries.append(query)
    if company:
        queries.extend([company, f"{company} news", f"{company} official website"])
        if role:
            queries.append(f"{company} {role}")
    if not queries:
        raise ToolError("Provide a query, or a company (and optional role).")

    limit = max(1, min(int(args.get("limit", 5)), 10))
    max_results = max(1, min(int(args.get("max_results", 8)), 15))
    backend = search.default_search_backend(ctx.get_settings())
    evidence = search.search_queries(
        queries, backend=backend, limit=limit, max_results=max_results
    )
    return {
        "queries": queries,
        "count": len(evidence),
        "results": [_evidence_payload(item) for item in evidence],
    }


def _tool_fetch_page(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    url = str(args.get("url") or "").strip()
    if not url:
        raise ToolError("Provide a url.")
    allow_domains = args.get("allow_domains") or []
    if isinstance(allow_domains, str):
        allow_domains = [part.strip() for part in allow_domains.split(",") if part.strip()]
    try:
        page = pages.fetch_page(
            url,
            allow_domains=allow_domains,
            max_chars=args.get("max_chars"),
            settings=ctx.get_settings(),
        )
    except pages.PageFetchError as exc:
        raise ToolError(str(exc)) from exc
    return page.to_dict()


def _tool_score_fit(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    session = ctx.session
    record: Opportunity | None = None
    payload: dict[str, Any] = dict(args.get("opportunity") or {})

    if args.get("opportunity_id") is not None:
        if session is None:
            raise ToolError("score_fit with an id needs a database session.")
        record = session.get(Opportunity, int(args["opportunity_id"]))
        if record is None:
            raise ToolError(f"No opportunity with id {args['opportunity_id']}.")
        payload = {**_opportunity_payload(record), **payload}
        parsed = (record.raw or {}).get("parsed_jd") if isinstance(record.raw, dict) else {}
        if isinstance(parsed, dict):
            for field in ("degree", "min_cgpa", "graduation_year"):
                if parsed.get(field) is not None:
                    payload.setdefault(field, parsed[field])

    if not payload:
        raise ToolError("Provide an opportunity_id or an opportunity object.")

    profile = args.get("preferences")
    if profile is None:
        if session is not None:
            profile = preferences.load_preferences(session).as_match_kwargs()
        else:
            profile = {}

    match = matching.score_opportunity(payload, profile)
    if args.get("persist") and record is not None:
        record.relevance_score = match.score
        record.relevance_reasons = match.reasons
        record.relevance_unknown = match.unknown
        record.eligible = match.hard_constraints_met
        ctx.require_session().flush()

    return {
        "opportunity_id": record.id if record else args.get("opportunity_id"),
        "score": match.score,
        "breakdown": match.breakdown,
        "reasons": match.reasons,
        "unknown": match.unknown,
        "hard_constraints_met": match.hard_constraints_met,
    }


def _tool_rank_portfolio(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    session = ctx.session
    opportunity: Opportunity | None = None
    if args.get("opportunity_id") is not None:
        if session is None:
            raise ToolError("rank_portfolio with an id needs a database session.")
        opportunity = session.get(Opportunity, int(args["opportunity_id"]))
        if opportunity is None:
            raise ToolError(f"No opportunity with id {args['opportunity_id']}.")

    requirements = args.get("requirements")
    if not requirements:
        requirements = _requirements_for(session, opportunity)
    requirements = [str(item) for item in (requirements or []) if str(item).strip()]

    items = args.get("portfolio_items")
    if not items:
        if session is None:
            raise ToolError("Provide portfolio_items or use a database session.")
        items = [
            {
                "id": item.id,
                "name": item.name,
                "description": item.description,
                "technologies": item.technologies or [],
                "achievements": item.achievements or [],
                "evidence_url": item.evidence_url,
                "status": item.status,
            }
            for item in session.scalars(select(PortfolioItem)).all()
        ]

    if not requirements:
        return {
            "opportunity_id": opportunity.id if opportunity else args.get("opportunity_id"),
            "requirements": [],
            "ranked": [],
            "possible_gaps": [],
            "note": "No JD requirements or skills are available, so nothing is ranked.",
        }

    ranked = resume.rank_portfolio_items(requirements, items)
    by_id = {int(item["id"]): item for item in items}
    limit = max(1, min(int(args.get("limit", 5)), 20))
    rows = []
    for item_id, score in ranked[:limit]:
        item = dict(by_id[item_id])
        item["score"] = score
        rows.append(item)

    return {
        "opportunity_id": opportunity.id if opportunity else args.get("opportunity_id"),
        "requirements": requirements,
        "ranked": rows,
        "possible_gaps": resume.skill_gaps(requirements, items),
        "note": "Gaps are keyword-based; a human must confirm before treating one as real.",
    }


def _tool_send_alert(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    session = ctx.require_session()
    settings = ctx.get_settings()
    title = str(args.get("title") or "Placement Copilot update")
    change_type = str(args.get("change_type") or "agent")
    channel = str(args.get("channel") or _default_channel(settings))
    message = args.get("message")

    payload: dict[str, Any] = {
        "title": title,
        "change_type": change_type,
    }
    for key in ("url", "next_action", "relevance_reason"):
        if args.get(key):
            payload[key] = args[key]
    if message:
        payload["message"] = str(message)

    dedup_key = str(args.get("dedup_key") or "").strip()
    if not dedup_key:
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]
        dedup_key = f"agent:{args.get('opportunity_id') or 'none'}:{change_type}:{digest}"

    delivery = change_detector.enqueue_alert(
        session,
        channel=channel,
        change_type=change_type,
        dedup_key=dedup_key,
        opportunity_id=args.get("opportunity_id"),
        payload=payload,
    )

    delivered = []
    if args.get("deliver") and delivery is not None:
        from app.services.alerts import deliver_pending

        delivered = [
            {"id": result.delivery_id, "ok": result.ok, "error": result.error}
            for result in deliver_pending(session, settings)
        ]

    return {
        "queued": delivery is not None,
        "delivery_id": delivery.id if delivery else None,
        "dedup_key": dedup_key,
        "channel": channel,
        "delivered": delivered,
    }


def _tool_research_company(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    company = str(args.get("company") or "").strip()
    if not company:
        raise ToolError("Provide a company.")
    report = research.generate_and_store(
        company,
        args.get("role"),
        force=bool(args.get("force")),
    )
    return {
        "company": report.company,
        "summary": report.summary,
        "sections": report.sections,
        "model": report.model,
        "generated_at": _iso(report.generated_at),
        "evidence": [_evidence_payload(item) for item in report.evidence],
    }


def _tool_get_student_profile(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    session = ctx.require_session()
    prefs = preferences.load_preferences(session)
    profile: dict[str, Any] = {
        "configured": prefs.is_configured(),
        "matching": prefs.matching,
        "notifications": prefs.notifications,
    }
    if args.get("include_portfolio", True):
        items = session.scalars(select(PortfolioItem).order_by(PortfolioItem.id)).all()
        profile["portfolio_count"] = len(items)
        profile["portfolio"] = [
            {
                "id": item.id,
                "name": item.name,
                "description": item.description,
                "technologies": item.technologies or [],
                "achievements": item.achievements or [],
                "evidence_url": item.evidence_url,
                "status": item.status,
            }
            for item in items
        ]
    return profile


# -- registry -----------------------------------------------------------


TOOLS: dict[str, Tool] = {
    "get_opportunity": Tool(
        name="get_opportunity",
        description=(
            "Load one stored opportunity by id or title/company query, including "
            "the JD, deadline, eligibility, skills and recent version changes."
        ),
        parameters={
            "type": "object",
            "properties": {
                "opportunity_id": {"type": "integer", "description": "Stored opportunity id."},
                "query": {
                    "type": "string",
                    "description": "Title or company substring when the id is unknown.",
                },
                "include_versions": {
                    "type": "boolean",
                    "default": True,
                    "description": "Include recent versions and field changes.",
                },
            },
            "additionalProperties": False,
        },
        handler=_tool_get_opportunity,
    ),
    "parse_jd": Tool(
        name="parse_jd",
        description=(
            "Extract skills, likely rounds, bond terms, pay, eligibility, location "
            "and deadline from a job description. Values not stated stay null and "
            "are listed in unknown_fields. Pass text or an opportunity_id."
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Raw JD text or HTML."},
                "opportunity_id": {
                    "type": "integer",
                    "description": "Use the stored JD when text is omitted.",
                },
                "title": {"type": "string"},
                "company": {"type": "string"},
                "persist": {
                    "type": "boolean",
                    "default": False,
                    "description": "Store the parsed result on the opportunity row.",
                },
            },
            "additionalProperties": False,
        },
        handler=_tool_parse_jd,
    ),
    "web_search": Tool(
        name="web_search",
        description=(
            "Search Wikipedia, recent news and other free sources for a company or "
            "role. Every result carries a source URL and type; results are cached."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "One search query."},
                "company": {
                    "type": "string",
                    "description": "Build several company-focused queries automatically.",
                },
                "role": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 15,
                    "default": 8,
                },
            },
            "additionalProperties": False,
        },
        handler=_tool_web_search,
    ),
    "fetch_page": Tool(
        name="fetch_page",
        description=(
            "Download an allow-listed cited page as plain text so a claim can be "
            "verified. Private addresses, unsupported content and huge pages are "
            "rejected. Add trusted domains with allow_domains for this call only."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The cited http(s) URL."},
                "allow_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Extra trusted domains for this call.",
                },
                "max_chars": {
                    "type": "integer",
                    "minimum": 500,
                    "maximum": 100000,
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        handler=_tool_fetch_page,
    ),
    "score_fit": Tool(
        name="score_fit",
        description=(
            "Score eligibility and personal fit for an opportunity using stored "
            "preferences. Missing profile or job fields are reported as unknown."
        ),
        parameters={
            "type": "object",
            "properties": {
                "opportunity_id": {"type": "integer"},
                "opportunity": {
                    "type": "object",
                    "description": "Inline opportunity fields when no id is available.",
                },
                "preferences": {
                    "type": "object",
                    "description": "Override stored preferences for this call.",
                },
                "persist": {
                    "type": "boolean",
                    "default": False,
                    "description": "Write score/reasons back to the opportunity row.",
                },
            },
            "additionalProperties": False,
        },
        handler=_tool_score_fit,
    ),
    "rank_portfolio": Tool(
        name="rank_portfolio",
        description=(
            "Rank verified portfolio projects against JD requirements and list "
            "possible gaps. Only real projects with evidence are returned."
        ),
        parameters={
            "type": "object",
            "properties": {
                "opportunity_id": {"type": "integer"},
                "requirements": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit requirements; defaults to the stored JD.",
                },
                "portfolio_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "technologies": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "achievements": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "evidence_url": {"type": "string"},
                            "status": {"type": "string"},
                        },
                        "required": ["id", "name"],
                    },
                    "description": "Inline portfolio records instead of the database.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
            },
            "additionalProperties": False,
        },
        handler=_tool_rank_portfolio,
    ),
    "send_alert": Tool(
        name="send_alert",
        description=(
            "Queue one deduplicated notification (Telegram/email/dashboard) from "
            "report facts. Delivery is handled by the existing alert worker."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "message": {
                    "type": "string",
                    "description": "Pre-rendered message; omit to let the renderer build it.",
                },
                "change_type": {"type": "string", "default": "agent"},
                "opportunity_id": {"type": "integer"},
                "channel": {
                    "type": "string",
                    "enum": ["telegram", "email", "dashboard"],
                },
                "dedup_key": {"type": "string"},
                "url": {"type": "string"},
                "next_action": {"type": "string"},
                "relevance_reason": {"type": "string"},
                "deliver": {
                    "type": "boolean",
                    "default": False,
                    "description": "Also drain the queue immediately (manual/testing).",
                },
            },
            "additionalProperties": False,
        },
        handler=_tool_send_alert,
    ),
    "research_company": Tool(
        name="research_company",
        description=(
            "Generate or reuse a cited company/role brief from collected evidence. "
            "Returns null sections when the evidence does not support a claim."
        ),
        parameters={
            "type": "object",
            "properties": {
                "company": {"type": "string"},
                "role": {"type": "string"},
                "force": {
                    "type": "boolean",
                    "default": False,
                    "description": "Ignore a stored brief and research again.",
                },
            },
            "required": ["company"],
            "additionalProperties": False,
        },
        handler=_tool_research_company,
    ),
    "get_student_profile": Tool(
        name="get_student_profile",
        description=(
            "Load the student's matching preferences, notification settings and "
            "verified portfolio items. This is the agent's memory, not advice."
        ),
        parameters={
            "type": "object",
            "properties": {
                "include_portfolio": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
        handler=_tool_get_student_profile,
    ),
}


def get_tool(name: str) -> Tool:
    try:
        return TOOLS[name]
    except KeyError as exc:
        raise ToolError(
            f"Unknown tool {name!r}. Available: {', '.join(sorted(TOOLS))}"
        ) from exc


def tool_specs() -> list[dict[str, Any]]:
    """JSON-schema descriptions for prompts and the CLI."""
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        }
        for tool in TOOLS.values()
    ]


def openai_tool_schemas() -> list[dict[str, Any]]:
    """Schemas in the OpenAI/Ollama function-calling shape."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in TOOLS.values()
    ]


def call_tool(
    name: str,
    arguments: dict[str, Any] | None = None,
    context: ToolContext | None = None,
) -> dict[str, Any]:
    """Run a tool and return ``{ok, tool, result|error}``.

    Failures are returned rather than raised so a model can read the error and
    try a different approach; the graph never crashes on a bad tool call.
    """
    context = context or ToolContext()
    try:
        tool = get_tool(name)
        if arguments is not None and not isinstance(arguments, dict):
            raise ToolError("Tool arguments must be a JSON object.")
        result = tool.handler(context, arguments or {})
        return {"ok": True, "tool": name, "result": result}
    except ToolError as exc:
        logger.info("Tool %s rejected: %s", name, exc)
        return {"ok": False, "tool": name, "error": str(exc)}
    except Exception as exc:
        logger.exception("Tool %s failed", name)
        return {"ok": False, "tool": name, "error": f"{type(exc).__name__}: {exc}"}
