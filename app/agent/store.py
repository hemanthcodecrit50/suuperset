"""Persistence for agent output: opportunity briefs and cached company evidence."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.planner import COMPANY_LEVEL_TOPICS
from app.agent.state import AgentState
from app.models import Company, ResearchReport
from app.services.research import Evidence

BRIEF_KIND = "agent_brief"
COMPANY_EVIDENCE_KIND = "agent_company"


def find_current_brief(
    session: Session, opportunity_id: int, content_hash: str | None
) -> ResearchReport | None:
    """The stored brief for this exact version of the posting, if one exists."""
    return session.scalar(
        select(ResearchReport)
        .where(ResearchReport.opportunity_id == opportunity_id)
        .where(ResearchReport.kind == BRIEF_KIND)
        .where(ResearchReport.content_hash == content_hash)
        .order_by(ResearchReport.id.desc())
        .limit(1)
    )


def save_brief(
    session: Session, state: AgentState, markdown: str, content_hash: str | None
) -> ResearchReport:
    row = ResearchReport(
        company_id=_company_id(session, state.company),
        opportunity_id=state.opportunity["id"],
        kind=BRIEF_KIND,
        summary=state.route_reason,
        content={**state.to_dict(), "markdown": markdown},
        sources=_source_rows(state.verified_evidence),
        model=None,
        content_hash=content_hash,
        generated_at=dt.datetime.now(dt.UTC),
    )
    session.add(row)
    session.flush()
    return row


def load_company_evidence(
    session: Session, company: str, max_age_days: int
) -> list[Evidence]:
    """Verified evidence stored for a company, unless it has gone stale."""
    company_row = session.scalar(select(Company).where(Company.name == company))
    if company_row is None:
        return []
    oldest_allowed = dt.datetime.now(dt.UTC) - dt.timedelta(days=max_age_days)
    row = session.scalar(
        select(ResearchReport)
        .where(ResearchReport.company_id == company_row.id)
        .where(ResearchReport.kind == COMPANY_EVIDENCE_KIND)
        .order_by(ResearchReport.id.desc())
        .limit(1)
    )
    if row is None or _is_older_than(row.generated_at, oldest_allowed):
        return []
    return [_evidence_from_source(item) for item in row.sources or []]


def save_company_evidence(session: Session, state: AgentState) -> None:
    """Cache this company's verified evidence so the next posting skips the searches."""
    evidence = [
        item for item in state.verified_evidence if item.topic in COMPANY_LEVEL_TOPICS
    ]
    if not evidence or not state.company:
        return
    session.add(
        ResearchReport(
            company_id=_company_id(session, state.company),
            kind=COMPANY_EVIDENCE_KIND,
            summary=f"Verified evidence for {state.company}",
            content={},
            sources=_source_rows(evidence),
            generated_at=dt.datetime.now(dt.UTC),
        )
    )
    session.flush()


def _company_id(session: Session, name: str) -> int | None:
    if not name:
        return None
    company = session.scalar(select(Company).where(Company.name == name))
    if company is None:
        company = Company(name=name)
        session.add(company)
        session.flush()
    return company.id


def _is_older_than(generated_at: dt.datetime | None, limit: dt.datetime) -> bool:
    if generated_at is None:
        return True
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=dt.UTC)
    return generated_at < limit


def _source_rows(evidence: list[Evidence]) -> list[dict[str, Any]]:
    return [
        {
            "url": item.source_url,
            "claim": item.claim,
            "type": item.source_type,
            "title": item.title,
            "published_at": item.published_at,
            "excerpt": item.excerpt,
            "topic": item.topic,
        }
        for item in evidence
    ]


def _evidence_from_source(item: dict[str, Any]) -> Evidence:
    return Evidence(
        claim=item["claim"],
        source_url=item["url"],
        source_type=item.get("type", "web"),
        title=item.get("title"),
        published_at=item.get("published_at"),
        excerpt=item.get("excerpt"),
        topic=item.get("topic", "general"),
    )
