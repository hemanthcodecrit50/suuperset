"""Personal application tracker.

Statuses mirror the portal's own vocabulary (verified from the status filter):
saved, eligible, applied, shortlisted, assessment, interview, offer,
offer_accepted, offer_rejected, rejected, withdrawn, not_eligible.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Application, Opportunity

logger = logging.getLogger(__name__)

STATUSES = (
    "saved",
    "eligible",
    "applied",
    "shortlisted",
    "assessment",
    "interview",
    "offer",
    "offer_accepted",
    "offer_rejected",
    "rejected",
    "withdrawn",
    "not_eligible",
)

# Statuses that mean the process has finished (no further action expected).
CLOSED_STATUSES = ("offer_accepted", "offer_rejected", "rejected", "withdrawn", "not_eligible")


@dataclass
class TrackResult:
    application: Application
    created: bool


def _parse_when(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(text, fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    return None


def find_opportunity(
    session: Session, query: str, opportunity_id: int | None = None
) -> Opportunity | None:
    """Resolve an opportunity by id, then by title/company substring."""
    if opportunity_id is not None:
        return session.get(Opportunity, opportunity_id)

    needle = query.strip()
    if not needle:
        return None
    like = f"%{needle}%"
    exact_title = session.scalar(
        select(Opportunity).where(Opportunity.title.ilike(like)).order_by(
            Opportunity.id.desc()
        )
    )
    if exact_title is not None:
        return exact_title
    return session.scalar(
        select(Opportunity).where(Opportunity.company_name.ilike(like)).order_by(
            Opportunity.id.desc()
        )
    )


def track(
    session: Session,
    opportunity: Opportunity,
    *,
    status: str = "saved",
    priority: int | None = None,
    next_action: str | None = None,
    next_action_at: str | None = None,
    notes: str | None = None,
    mark_applied: bool = False,
) -> TrackResult:
    """Create or update the application row for ``opportunity``."""
    status = status.strip().lower()
    if status not in STATUSES:
        raise ValueError(f"Unknown status {status!r}. Valid: {', '.join(STATUSES)}")

    application = session.scalar(
        select(Application).where(Application.opportunity_id == opportunity.id)
    )
    created = application is None
    if application is None:
        application = Application(opportunity_id=opportunity.id, status=status)
        session.add(application)

    application.status = status
    if priority is not None:
        application.priority = priority
    if next_action is not None:
        application.next_action = next_action
    if notes is not None:
        application.notes = notes
    parsed_when = _parse_when(next_action_at)
    if parsed_when is not None:
        application.next_action_at = parsed_when
    if mark_applied or status in ("applied", "offer_accepted"):
        if application.applied_at is None:
            application.applied_at = dt.datetime.now(dt.timezone.utc)

    session.flush()
    return TrackResult(application=application, created=created)


def upcoming_actions(session: Session, days: int = 7) -> list[tuple[Application, Opportunity]]:
    """Applications with an action due inside ``days``."""
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now + dt.timedelta(days=days)
    rows = session.execute(
        select(Application, Opportunity)
        .join(Opportunity, Application.opportunity_id == Opportunity.id)
        .where(Application.next_action_at.is_not(None))
        .where(Application.next_action_at >= now)
        .where(Application.next_action_at <= cutoff)
        .order_by(Application.next_action_at)
    ).all()
    return [(app, opp) for app, opp in rows]


def open_applications(session: Session) -> list[tuple[Application, Opportunity]]:
    """Everything not yet closed."""
    rows = session.execute(
        select(Application, Opportunity)
        .join(Opportunity, Application.opportunity_id == Opportunity.id)
        .order_by(Application.priority.desc().nullslast(), Application.id.desc())
    ).all()
    return [(app, opp) for app, opp in rows if app.status not in CLOSED_STATUSES]