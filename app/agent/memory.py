"""Load what the agent remembers about the student before it reads a posting."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import store
from app.agent.router import IN_PROCESS_STATUSES
from app.agent.state import StudentMemory
from app.config import Settings
from app.models import Application, Opportunity, PortfolioItem
from app.services.preferences import load_preferences

IN_PROCESS_APPLICATION_STATUSES = frozenset(
    status.lower() for status in IN_PROCESS_STATUSES
) | {"offer"}


def load_memory(session: Session, company: str, settings: Settings) -> StudentMemory:
    """Preferences, real portfolio projects and any cached evidence for the company."""
    preferences = load_preferences(session)
    cached_evidence = store.load_company_evidence(
        session, company, settings.agent_company_cache_days
    )
    return StudentMemory(
        match_profile=preferences.as_match_kwargs(),
        portfolio=_portfolio_records(session),
        cached_evidence=cached_evidence,
        is_company_familiar=preferences.always_notify_company(company)
        or _has_tracked_application(session, company),
        is_company_applied=_has_in_process_application(session, company),
        is_profile_set=preferences.is_configured(),
    )


def _portfolio_records(session: Session) -> list[dict]:
    return [
        {
            "id": item.id,
            "name": item.name,
            "description": item.description,
            "technologies": item.technologies or [],
            "achievements": item.achievements or [],
            "evidence_url": item.evidence_url,
            "status": item.status,
        }
        for item in session.scalars(select(PortfolioItem).order_by(PortfolioItem.id))
    ]


def _application_statuses(session: Session, company: str) -> list[str]:
    if not company:
        return []
    return list(
        session.scalars(
            select(Application.status)
            .join(Opportunity, Opportunity.id == Application.opportunity_id)
            .where(Opportunity.company_name == company)
        )
    )


def _has_tracked_application(session: Session, company: str) -> bool:
    return bool(_application_statuses(session, company))


def _has_in_process_application(session: Session, company: str) -> bool:
    return any(
        status in IN_PROCESS_APPLICATION_STATUSES
        for status in _application_statuses(session, company)
    )
