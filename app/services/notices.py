"""Superset notices: which ones concern the student, and the schedule inside them."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.agent.planner import search_name
from app.collector.mappers import NOTICE_CATEGORY
from app.models import Announcement, Application, Opportunity

IN_PROCESS_APPLICATION_STATUSES = frozenset(
    {"applied", "shortlisted", "assessment", "interview", "offer", "offer_accepted"}
)
# Words that are too common in company names to identify one on their own
GENERIC_NAME_WORDS = frozenset(
    {
        "the",
        "india",
        "technologies",
        "technology",
        "solutions",
        "systems",
        "software",
        "services",
        "global",
        "labs",
        "group",
    }
)
WEB_ADDRESS_PATTERN = re.compile(r"(?:https?://|www\.)\S+")
MIN_KEY_LENGTH = 3
MIN_TYPO_KEY_LENGTH = 5
# Company names that begin with an everyday word need their second word too
COMMON_ENGLISH_STARTS = frozenset(
    {
        "future",
        "applied",
        "smart",
        "digital",
        "first",
        "general",
        "united",
        "national",
        "standard",
        "advanced",
        "data",
        "cloud",
        "next",
        "new",
        "open",
        "bright",
        "blue",
        "green",
        "red",
        "prime",
        "alpha",
        "deep",
        "true",
        "great",
        "royal",
        "silver",
        "golden",
        "california",
        "texas",
        "american",
        "indian",
    }
)
SCHEDULE_TABLE_MIN_CELLS = 6
PROCESS_TITLE_PATTERN = re.compile(
    r"process|schedule|assessment|test|interview|shortlist|result|round|venue",
    re.IGNORECASE,
)


@dataclass
class NoticeView:
    record: Announcement
    company: str | None

    @property
    def schedule(self) -> list[dict]:
        return (self.record.raw or {}).get("schedule") or []

    @property
    def companies(self) -> list[str]:
        """Every applied company the notice concerns; a timetable can name several."""
        named = [event["company"] for event in self.schedule if event.get("company")]
        ordered = [self.company] if self.company else []
        return list(dict.fromkeys(ordered + named))

    @property
    def attachments(self) -> list[dict]:
        return (self.record.raw or {}).get("attachments") or []


@dataclass
class UpcomingEvent:
    when: dt.date
    time: str | None
    what: str
    venue: str | None
    notes: str | None
    company: str | None
    notice: Announcement
    is_tentative: bool = False
    schedule_note: str | None = None


def applied_companies(session: Session) -> list[str]:
    """Companies the student has applied to, on Superset or in this app."""
    names: set[str] = set()
    for record in session.scalars(select(Opportunity)):
        if (record.raw or {}).get(
            "application_status"
        ) == "APPLIED" and record.company_name:
            names.add(record.company_name)
    tracked = session.execute(
        select(Opportunity.company_name, Application.status).join(
            Application, Application.opportunity_id == Opportunity.id
        )
    )
    for company, status in tracked:
        if company and status in IN_PROCESS_APPLICATION_STATUSES:
            names.add(company)
    return sorted(names)


def mentioned_company(text: str, companies: list[str]) -> str | None:
    """The first company whose distinctive name appears in the text, if any."""
    # A link such as amazon.in/some-book is not a mention of Amazon
    lowered = WEB_ADDRESS_PATTERN.sub(" ", text.lower())
    for company in companies:
        key = company_key(company)
        if key and re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", lowered):
            return company
    return None


def company_in_cell(cell: str, companies: list[str]) -> str | None:
    """Company named in one timetable cell, forgiving a one-letter typo like "Dayfoce"."""
    exact = mentioned_company(cell, companies)
    if exact is not None:
        return exact
    words = re.findall(r"[a-z0-9]+", cell.lower())
    for company in companies:
        key = company_key(company)
        if " " in key or len(key) < MIN_TYPO_KEY_LENGTH:
            continue
        if any(_within_one_edit(word, key) for word in words):
            return company
    return None


def _within_one_edit(word: str, key: str) -> bool:
    # One letter added, removed or changed: enough for typos without confusing names
    if abs(len(word) - len(key)) > 1:
        return False
    if len(word) == len(key):
        return sum(a != b for a, b in zip(word, key, strict=True)) <= 1
    shorter, longer = sorted((word, key), key=len)
    return any(
        longer[:index] + longer[index + 1 :] == shorter for index in range(len(longer))
    )


def company_key(company: str) -> str:
    """Distinctive word of a company name: "Cohesity India Pvt Ltd" gives "cohesity".

    When that word is ordinary English ("Future Standard", "Applied Materials") the
    next word is added, so the name does not match every notice using the word.
    """
    words = [word.strip(".,-&'") for word in search_name(company).lower().split()]
    distinctive = [word for word in words if word and word not in GENERIC_NAME_WORDS]
    if not distinctive:
        return ""
    size = 2 if distinctive[0] in COMMON_ENGLISH_STARTS else 1
    key = " ".join(distinctive[:size])
    return key if len(key) >= MIN_KEY_LENGTH else ""


def is_schedule_table(title: str, text: str) -> bool:
    """A placement-cell timetable listing many companies, like the weekly schedule."""
    return "schedule" in title.lower() and text.count("|") >= SCHEDULE_TABLE_MIN_CELLS


def is_application_opening(title: str) -> bool:
    # Its only date is the application deadline, which the posting already shows
    return title.lower().startswith("open for applications")


def notice_feed(
    session: Session, *, only_applied: bool, limit: int = 100
) -> list[NoticeView]:
    """Full notices newest first, each tagged with the applied company it mentions."""
    companies = applied_companies(session)
    views: list[NoticeView] = []
    for record in _notices(session, limit=limit if not only_applied else None):
        company = mentioned_company(f"{record.title}\n{record.body or ''}", companies)
        if only_applied and company is None:
            continue
        views.append(NoticeView(record, company))
        if len(views) >= limit:
            break
    return views


def related_notices(
    session: Session, company: str | None, limit: int = 5
) -> list[Announcement]:
    """Notices that mention one company, newest first."""
    if not company or not company_key(company):
        return []
    return [
        record
        for record in _notices(session, limit=None)
        if mentioned_company(f"{record.title}\n{record.body or ''}", [company])
    ][:limit]


def recent_applied_count(session: Session, days: int = 7) -> int:
    """Notices from the last few days that mention a company the student applied to."""
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    companies = applied_companies(session)
    return sum(
        1
        for record in _notices(session, limit=None)
        if _is_after(record.published_at, since)
        and mentioned_company(f"{record.title}\n{record.body or ''}", companies)
    )


def upcoming_events(session: Session, limit: int = 20) -> list[UpcomingEvent]:
    """Dated events pulled from notices, from today onwards, soonest first.

    When a specific notice covers the same company on the same day, that event is
    shown once (it has the real time and venue) and notes the timetable entry.
    """
    today = dt.date.today()
    events: list[UpcomingEvent] = []
    for record in _notices(session, limit=None):
        raw = record.raw or {}
        for item in raw.get("schedule") or []:
            when = _parse_date(item.get("date"))
            if when is None or when < today:
                continue
            events.append(
                UpcomingEvent(
                    when=when,
                    time=item.get("time"),
                    what=item.get("what") or record.title,
                    venue=item.get("venue"),
                    notes=item.get("notes"),
                    company=item.get("company") or raw.get("company"),
                    notice=record,
                    is_tentative=bool(item.get("tentative")),
                )
            )
    confirmed = {
        (event.company, event.when) for event in events if not event.is_tentative
    }
    tentative = {
        (event.company, event.when): event.what
        for event in events
        if event.is_tentative
    }
    kept: list[UpcomingEvent] = []
    for event in events:
        key = (event.company, event.when)
        if event.is_tentative and key in confirmed:
            continue
        if not event.is_tentative and key in tentative:
            event.schedule_note = f"Also in the weekly schedule: {tentative[key]}"
        kept.append(event)
    return sorted(kept, key=lambda event: (event.when, event.time or ""))[:limit]


def notices_needing_schedule(
    session: Session, *, max_age_days: int, limit: int
) -> list[tuple[Announcement, str]]:
    """Recent unread notices about applied companies, newest first, for schedule extraction."""
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=max_age_days)
    companies = applied_companies(session)
    pending: list[tuple[Announcement, str]] = []
    for record in _notices(session, limit=None):
        if (record.raw or {}).get("schedule_checked") or not _is_after(
            record.published_at, since
        ):
            continue
        company = mentioned_company(f"{record.title}\n{record.body or ''}", companies)
        if company is not None:
            pending.append((record, company))
    # Process and assessment notices carry the dates that matter, so they go first
    pending.sort(key=lambda pair: not PROCESS_TITLE_PATTERN.search(pair[0].title))
    return pending[:limit]


def _notices(session: Session, limit: int | None) -> list[Announcement]:
    statement = (
        select(Announcement)
        .where(Announcement.category == NOTICE_CATEGORY)
        .order_by(desc(Announcement.published_at), desc(Announcement.id))
    )
    if limit is not None:
        statement = statement.limit(limit)
    return list(session.scalars(statement))


def _is_after(value: dt.datetime | None, since: dt.datetime) -> bool:
    if value is None:
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.UTC)
    return value >= since


def _parse_date(value: object) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        return None
