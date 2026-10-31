"""Company and job research service.

Two-stage pipeline: gather evidence first, then generate the report. Never ask
a model to produce a company report from memory alone, and never present
unsupported claims as facts.

This module provides the structure and caching; the search backend and model
call are intentionally abstracted so a free API or a local Ollama model can be
substituted without changing callers.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import select

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass
class Evidence:
    """A single sourced fact used to ground a report."""

    claim: str
    source_url: str
    source_type: str = "web"  # official | news | reference | anecdotal | web
    title: str | None = None
    published_at: str | None = None
    excerpt: str | None = None


@dataclass
class ResearchReport:
    company: str
    summary: str = ""
    sections: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    model: str | None = None
    generated_at: dt.datetime | None = None
    content_hash: str | None = None


class SearchBackend(Protocol):
    """Pluggable search backend (free API quota, or local)."""

    def search(self, query: str, limit: int = 5) -> list[Evidence]: ...


class NullSearchBackend:
    """Placeholder backend that returns no evidence.

    Replace with a real, terms-compliant search implementation. Returning no
    evidence forces the report generator to stay honest about what is unknown.
    """

    def search(self, query: str, limit: int = 5) -> list[Evidence]:
        logger.warning("NullSearchBackend cannot search for %r", query)
        return []


def _hash_report(report: ResearchReport) -> str:
    blob = json.dumps(
        {
            "company": report.company,
            "summary": report.summary,
            "sections": report.sections,
            "evidence": [e.__dict__ for e in report.evidence],
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def gather_evidence(
    company: str,
    role: str | None = None,
    backend: SearchBackend | None = None,
    *,
    settings: Settings | None = None,
) -> list[Evidence]:
    """Stage 1: collect sources for a company/role.

    Defaults to the composite backend (Wikipedia + recent news + optional
    Brave, with DuckDuckGo as a fallback). Any failure yields no evidence,
    which forces the report to stay honest about what is unknown.
    """
    if backend is None:
        try:
            from app.services.search import default_search_backend

            backend = default_search_backend(settings)
        except Exception as exc:  # noqa: BLE001 - never break collection
            logger.warning("No search backend available: %s", exc)
            backend = NullSearchBackend()

    queries = [
        # The bare name is far likeliest to return a Wikipedia-style abstract.
        company,
        f"{company} company",
        f"{company} products services",
        f"{company} engineering technology",
    ]
    if role:
        queries.append(f"{company} {role}")

    evidence: list[Evidence] = []
    seen: set[str] = set()
    # Stop early: two solid sources are enough for a grounded brief.
    for query in queries:
        if len(evidence) >= 4:
            break
        for item in backend.search(query):
            key = item.claim[:120]
            if key in seen:
                continue
            seen.add(key)
            evidence.append(item)
    return evidence


def generate_report(
    company: str,
    role: str | None = None,
    *,
    backend: SearchBackend | None = None,
    settings: Settings | None = None,
) -> ResearchReport:
    """Stage 2: produce a structured, cited report from gathered evidence."""
    settings = settings or get_settings()
    evidence = gather_evidence(company, role, backend=backend, settings=settings)

    report = ResearchReport(
        company=company,
        summary="",
        sections={
            "overview": None,
            "products": None,
            "recent_developments": None,
            "engineering_culture": None,
            "role_responsibilities": None,
            "compensation": None,
            "concerns": None,
        },
        evidence=evidence,
        model=settings.research_model if evidence else None,
        generated_at=dt.datetime.now(dt.timezone.utc),
    )

    if not evidence:
        report.summary = (
            "No external sources could be collected, so no claims are made. "
            "Connect a search backend or check connectivity."
        )
        report.content_hash = _hash_report(report)
        return report

    # Generation is evidence-only; unsupported fields come back as null.
    try:
        from app.services.llm import REPORT_SYSTEM, default_client

        client = default_client()
        if not client.configured:
            report.summary = (
                "Sources were collected but no LLM is configured, so no "
                "analysis was generated. See the source list below."
            )
        else:
            payload = {
                "company": company,
                "role": role,
                "evidence": [
                    {
                        "claim": e.claim,
                        "source": e.source_url,
                        "type": e.source_type,
                        "title": e.title,
                        "published_at": e.published_at,
                    }
                    for e in evidence
                ],
            }
            generated = client.complete_json(REPORT_SYSTEM, str(payload))
            if generated:
                for key in report.sections:
                    value = generated.get(key)
                    report.sections[key] = str(value) if value else None
                report.summary = str(generated.get("summary") or "")
                report.sections["recommendation"] = generated.get("recommendation")
                report.model = settings.research_model
            else:
                # Most often a bad key, wrong base URL, or an exhausted quota.
                report.summary = (
                    f"Sources were collected, but the model call to "
                    f"'{settings.research_model}' failed (check OPENAI_API_KEY / "
                    f"OPENAI_BASE_URL and your quota). No analysis was generated."
                )
    except Exception as exc:  # noqa: BLE001 - research must not crash the CLI
        logger.warning("Report generation failed: %s", exc)
        report.summary = f"Report generation failed: {exc}"

    report.content_hash = _hash_report(report)
    return report


def generate_and_store(
    company: str, role: str | None = None, force: bool = False
) -> ResearchReport:
    """Generate a report and persist it to ``research_reports``.

    A previously stored report is reused unless ``force`` is set, so searching
    is not re-run (and re-billed) for a company already researched.
    """
    from app.db import session_scope
    from app.models import Company, ResearchReport as ResearchReportRow

    if not force:
        with session_scope() as session:
            company_row = session.scalar(select(Company).where(Company.name == company))
            if company_row is not None:
                stored = session.scalars(
                    select(ResearchReportRow)
                    .where(ResearchReportRow.company_id == company_row.id)
                    .where(ResearchReportRow.kind == ("role" if role else "company"))
                    .order_by(ResearchReportRow.id.desc())
                    .limit(1)
                ).first()
                if stored is not None and stored.sources:
                    logger.info("Reusing stored research for %s", company)
                    return _report_from_row(stored, company)

    report = generate_report(company, role)

    with session_scope() as session:
        company_row = session.scalar(
            select(Company).where(Company.name == company)
        )
        if company_row is None:
            company_row = Company(name=company)
            session.add(company_row)
            session.flush()

        row = ResearchReportRow(
            company_id=company_row.id,
            kind="role" if role else "company",
            summary=report.summary,
            content=report.sections,
            sources=[
                {
                    "url": e.source_url,
                    "claim": e.claim,
                    "type": e.source_type,
                    "title": e.title,
                    "published_at": e.published_at,
                    "excerpt": e.excerpt,
                }
                for e in report.evidence
            ],
            model=report.model,
            content_hash=report.content_hash,
            generated_at=report.generated_at,
        )
        session.add(row)
        session.flush()

    return report


def _report_from_row(row, company: str) -> ResearchReport:
    """Rebuild a ResearchReport from a stored row (cached path)."""
    return ResearchReport(
        company=company,
        summary=row.summary or "",
        sections=dict(row.content or {}),
        evidence=[
            Evidence(
                claim=item.get("claim", ""),
                source_url=item.get("url", ""),
                source_type=item.get("type", "web"),
                title=item.get("title"),
                published_at=item.get("published_at"),
                excerpt=item.get("excerpt"),
            )
            for item in (row.sources or [])
            if isinstance(item, dict)
        ],
        model=row.model,
        generated_at=row.generated_at,
        content_hash=row.content_hash,
    )
