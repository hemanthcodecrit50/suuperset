"""Read models for the web pages, built from the existing tables."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from app.agent.store import BRIEF_KIND
from app.models import (
    Announcement,
    Application,
    Opportunity,
    PollRun,
    ResearchReport,
    ResumeSuggestion,
)
from app.services.preferences import load_preferences
from app.ui.common import as_utc, days_until

NEW_POSTING_DAYS = 7
CLOSING_SOON_DAYS = 3
INBOX_LIMIT = 300
# Portal statuses meaning the student has acted, unlike the default "yet to apply"
PORTAL_PROGRESS_STATUSES = frozenset(
    {"applied", "shortlisted", "assessment", "interview", "offered", "offer_accepted"}
)
INBOX_VIEWS = ("open", "new", "briefed", "tracked", "all")


@dataclass
class PostingRow:
    id: int
    title: str
    company: str
    location: str | None
    fit_score: float | None
    eligible: bool | None
    deadline: dt.datetime | None
    days_left: int | None
    status: str | None
    is_new: bool
    has_brief: bool
    is_tracked: bool


def inbox_rows(session: Session, view: str, query: str) -> list[PostingRow]:
    """Postings for the inbox, newest first, narrowed by view and search text."""
    statement = select(Opportunity).where(Opportunity.is_active.is_(True))
    if query.strip():
        # Search text is literal: "%" and "_" must not act as wildcards
        escaped = re.sub(r"([\\%_])", r"\\\1", query.strip())
        like = f"%{escaped}%"
        statement = statement.where(
            or_(
                Opportunity.title.ilike(like, escape="\\"),
                Opportunity.company_name.ilike(like, escape="\\"),
            )
        )
    records = session.scalars(
        statement.order_by(desc(Opportunity.first_seen_at), desc(Opportunity.id)).limit(
            INBOX_LIMIT
        )
    ).all()
    context = _RowContext(
        briefed_ids=_briefed_opportunity_ids(session),
        statuses=_application_statuses(session),
        backlog_loaded_at=_backlog_loaded_at(session),
        is_profile_set=load_preferences(session).is_configured(),
    )
    rows = [_posting_row(record, context) for record in records]
    return sorted((row for row in rows if _matches_view(row, view)), key=_inbox_order)


@dataclass
class InboxStats:
    new_this_week: int
    closing_soon: int
    briefs_ready: int
    pending_approvals: int
    view_counts: dict[str, int]


def inbox_stats(session: Session) -> InboxStats:
    """Headline counts for the strip at the top of the inbox."""
    rows = inbox_rows(session, "all", "")
    return InboxStats(
        new_this_week=sum(row.is_new for row in rows),
        closing_soon=sum(
            row.days_left is not None and 0 <= row.days_left <= CLOSING_SOON_DAYS
            for row in rows
        ),
        briefs_ready=sum(row.has_brief for row in rows),
        pending_approvals=pending_approval_count(session),
        view_counts={
            view: sum(_matches_view(row, view) for row in rows) for view in INBOX_VIEWS
        },
    )


def posting_brief(session: Session, opportunity_id: int) -> ResearchReport | None:
    return session.scalar(
        select(ResearchReport)
        .where(ResearchReport.opportunity_id == opportunity_id)
        .where(ResearchReport.kind == BRIEF_KIND)
        .order_by(desc(ResearchReport.id))
        .limit(1)
    )


def posting_status(session: Session, record: Opportunity) -> str | None:
    application = session.scalar(
        select(Application).where(Application.opportunity_id == record.id)
    )
    if application is not None:
        return application.status
    return _portal_status(record)


@dataclass
class ApplicationRow:
    opportunity: Opportunity
    application: Application | None
    days_left: int | None

    @property
    def status(self) -> str | None:
        if self.application is not None:
            return self.application.status
        return _portal_status(self.opportunity)

    @property
    def is_applied_on_superset(self) -> bool:
        return (self.opportunity.raw or {}).get("application_status") == "APPLIED"


def tracked_applications(session: Session) -> list[ApplicationRow]:
    """Jobs tracked here or applied to on Superset, soonest deadline first."""
    tracked = {
        application.opportunity_id: application
        for application in session.scalars(select(Application))
    }
    records = session.scalars(
        select(Opportunity).order_by(
            Opportunity.deadline.is_(None), desc(Opportunity.deadline)
        )
    )
    rows = [
        ApplicationRow(record, tracked.get(record.id), days_until(record.deadline))
        for record in records
        if record.id in tracked
        or (record.raw or {}).get("application_status") == "APPLIED"
    ]
    return sorted(rows, key=_application_order)


def _application_order(row: ApplicationRow) -> tuple[int, float]:
    # Upcoming deadlines first (soonest on top), then everything already closed, newest first
    deadline = as_utc(row.opportunity.deadline)
    if deadline is not None and row.days_left is not None and row.days_left >= 0:
        return (0, deadline.timestamp())
    return (1, -deadline.timestamp() if deadline else 0.0)


def job_alerts(session: Session, limit: int = 100) -> list[Announcement]:
    """Portal alerts about job profiles (opened, updated), newest first."""
    return list(
        session.scalars(
            select(Announcement)
            .where(Announcement.category == "JOBPROFILE")
            .order_by(desc(Announcement.published_at))
            .limit(limit)
        )
    )


def suggestions(
    session: Session, *, is_approved: bool
) -> list[tuple[ResumeSuggestion, Opportunity | None]]:
    rows = session.execute(
        select(ResumeSuggestion, Opportunity)
        .outerjoin(Opportunity, ResumeSuggestion.opportunity_id == Opportunity.id)
        .where(ResumeSuggestion.approved.is_(is_approved))
        .order_by(desc(ResumeSuggestion.id))
    ).all()
    return [(suggestion, opportunity) for suggestion, opportunity in rows]


def pending_approval_count(session: Session) -> int:
    return (
        session.scalar(
            select(func.count(ResumeSuggestion.id)).where(
                ResumeSuggestion.approved.is_(False)
            )
        )
        or 0
    )


def recent_poll_runs(session: Session, limit: int = 5) -> list[PollRun]:
    return list(
        session.scalars(select(PollRun).order_by(desc(PollRun.id)).limit(limit))
    )


@dataclass
class _RowContext:
    briefed_ids: set[int]
    statuses: dict[int, str]
    backlog_loaded_at: dt.datetime | None
    is_profile_set: bool


def _posting_row(record: Opportunity, context: _RowContext) -> PostingRow:
    return PostingRow(
        id=record.id,
        title=record.title,
        company=clean_company(record.company_name) or "Unknown company",
        location=clean_location(record.location),
        # Without a profile the score only reflects whether pay is listed
        fit_score=record.relevance_score if context.is_profile_set else None,
        eligible=record.eligible,
        deadline=as_utc(record.deadline),
        days_left=days_until(record.deadline),
        status=context.statuses.get(record.id) or _portal_status(record),
        is_new=_is_new(record.first_seen_at, context.backlog_loaded_at),
        has_brief=record.id in context.briefed_ids,
        is_tracked=record.id in context.statuses
        or _portal_status(record) in PORTAL_PROGRESS_STATUSES,
    )


def clean_location(location: str | None) -> str | None:
    """Drop placeholder locations such as '.' that the portal sometimes returns."""
    text = (location or "").strip(" .,-")
    return text if any(char.isalnum() for char in text) else None


def clean_company(name: str | None) -> str:
    """Company name without stray endings the portal leaves, like "Acme," or "Acme..".

    Display only: stored names stay as collected so content hashes do not change.
    """
    text = (name or "").strip().rstrip(",;-").strip()
    return re.sub(r"\.{2,}$", ".", text)


def _inbox_order(row: PostingRow) -> tuple[int, float]:
    # Open postings first, soonest deadline on top; closed ones last, newest first
    if row.deadline is None:
        return (1, 0.0)
    if row.days_left is not None and row.days_left >= 0:
        return (0, row.deadline.timestamp())
    return (2, -row.deadline.timestamp())


def _backlog_loaded_at(session: Session) -> dt.datetime | None:
    first_success = session.scalar(
        select(PollRun)
        .where(PollRun.status.in_(("success", "partial")))
        .order_by(PollRun.id)
        .limit(1)
    )
    return as_utc(first_success.finished_at) if first_success else None


def _matches_view(row: PostingRow, view: str) -> bool:
    if view == "new":
        return row.is_new
    if view == "briefed":
        return row.has_brief
    if view == "open":
        return row.days_left is None or row.days_left >= 0
    if view == "tracked":
        return row.is_tracked
    return True


def _briefed_opportunity_ids(session: Session) -> set[int]:
    return set(
        session.scalars(
            select(ResearchReport.opportunity_id)
            .where(ResearchReport.kind == BRIEF_KIND)
            .where(ResearchReport.opportunity_id.is_not(None))
        )
    )


def _application_statuses(session: Session) -> dict[int, str]:
    rows = session.execute(select(Application.opportunity_id, Application.status)).all()
    return {opportunity_id: status for opportunity_id, status in rows}


def _portal_status(record: Opportunity) -> str | None:
    raw: dict[str, Any] = record.raw if isinstance(record.raw, dict) else {}
    status = raw.get("application_status") or raw.get("job_profile_status")
    return str(status).lower() if status else None


def _is_new(
    first_seen_at: dt.datetime | None, backlog_loaded_at: dt.datetime | None
) -> bool:
    # Postings from the first poll are the existing backlog, not news
    first_seen_at = as_utc(first_seen_at)
    if first_seen_at is None:
        return False
    if backlog_loaded_at is not None and first_seen_at <= backlog_loaded_at:
        return False
    age = dt.datetime.now(dt.UTC) - first_seen_at
    return age <= dt.timedelta(days=NEW_POSTING_DAYS)
