"""SQLAlchemy ORM models for the placement intelligence database.

The models mirror ``sql/schema.sql``. Unknown values (deadlines, eligibility,
compensation) must stay ``NULL``/unknown rather than being guessed.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# BigInteger primary keys do not autoincrement on SQLite (used by the test
# suite). Use INTEGER there and BIGINT on PostgreSQL.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Source(TimestampMixin, Base):
    """A portal/source configuration."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    base_url: Mapped[str | None] = mapped_column(Text)
    login_url: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config: Mapped[dict | None] = mapped_column(JSON, default=dict)


class Company(TimestampMixin, Base):
    """Company metadata and research."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    slug: Mapped[str | None] = mapped_column(Text, unique=True)
    website: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(Text)
    headquarters: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, default=dict)

    opportunities: Mapped[list["Opportunity"]] = relationship(back_populates="company")


class Opportunity(TimestampMixin, Base):
    """A current job opening."""

    __tablename__ = "opportunities"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_opportunities_source_external"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    # Set by the deadline-reminder job once the user has been nudged.
    deadline_reminded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # When the full JD was last fetched; keeps per-job detail requests one-off.
    detail_fetched_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    company_name: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    job_function: Mapped[str | None] = mapped_column(Text)
    work_type: Mapped[str | None] = mapped_column(Text)
    compensation: Mapped[str | None] = mapped_column(Text)
    eligibility: Mapped[str | None] = mapped_column(Text)
    skills: Mapped[list | None] = mapped_column(JSON, default=list)
    deadline: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    apply_url: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    attachments: Mapped[list | None] = mapped_column(JSON, default=list)
    content_hash: Mapped[str | None] = mapped_column(Text)
    # Personal relevance (0-100) computed from user preferences at poll time.
    relevance_score: Mapped[float | None] = mapped_column(Float)
    relevance_reasons: Mapped[list | None] = mapped_column(JSON, default=list)
    relevance_unknown: Mapped[list | None] = mapped_column(JSON, default=list)
    eligible: Mapped[bool | None] = mapped_column(Boolean)
    first_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    raw: Mapped[dict | None] = mapped_column(JSON, default=dict)

    company: Mapped[Company | None] = relationship(back_populates="opportunities")
    versions: Mapped[list["OpportunityVersion"]] = relationship(back_populates="opportunity")


class OpportunityVersion(Base):
    """Historical snapshot of an opportunity, captured on change."""

    __tablename__ = "opportunity_versions"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    eligibility: Mapped[str | None] = mapped_column(Text)
    deadline: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    skills: Mapped[list | None] = mapped_column(JSON, default=list)
    raw: Mapped[dict | None] = mapped_column(JSON, default=dict)
    captured_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    opportunity: Mapped[Opportunity] = relationship(back_populates="versions")


class Announcement(TimestampMixin, Base):
    """A placement notification/announcement."""

    __tablename__ = "announcements"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_announcements_source_external"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    raw: Mapped[dict | None] = mapped_column(JSON, default=dict)

    versions: Mapped[list["AnnouncementVersion"]] = relationship(back_populates="announcement")


class AnnouncementVersion(Base):
    """Historical snapshot of an announcement, captured on change."""

    __tablename__ = "announcement_versions"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    announcement_id: Mapped[int] = mapped_column(
        ForeignKey("announcements.id", ondelete="CASCADE"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict | None] = mapped_column(JSON, default=dict)
    captured_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    announcement: Mapped[Announcement] = relationship(back_populates="versions")


class Application(TimestampMixin, Base):
    """Personal application tracker entry."""

    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(Text, default="saved", nullable=False)
    priority: Mapped[int | None] = mapped_column(Integer)
    next_action: Mapped[str | None] = mapped_column(Text)
    next_action_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    applied_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class UserPreference(TimestampMixin, Base):
    """Flexible key/value store for interests and notification preferences."""

    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)


class PortfolioItem(TimestampMixin, Base):
    """Verified project, skill, achievement and its evidence."""

    __tablename__ = "portfolio_items"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    technologies: Mapped[list | None] = mapped_column(JSON, default=list)
    achievements: Mapped[list | None] = mapped_column(JSON, default=list)
    evidence_url: Mapped[str | None] = mapped_column(Text)
    # planned | in_progress | implemented | completed
    status: Mapped[str] = mapped_column(Text, default="implemented", nullable=False)


class ResearchReport(TimestampMixin, Base):
    """Structured company/job research with source references."""

    __tablename__ = "research_reports"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"))
    kind: Mapped[str] = mapped_column(Text, default="company", nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    content: Mapped[dict | None] = mapped_column(JSON, default=dict)
    sources: Mapped[list | None] = mapped_column(JSON, default=list)
    model: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(Text)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    generated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class ResumeSuggestion(TimestampMixin, Base):
    """A proposed resume bullet linked to a project and an opportunity."""

    __tablename__ = "resume_suggestions"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"))
    portfolio_item_id: Mapped[int | None] = mapped_column(ForeignKey("portfolio_items.id"))
    requirement: Mapped[str | None] = mapped_column(Text)
    bullet: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text)
    technologies: Mapped[list | None] = mapped_column(JSON, default=list)
    approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AlertDelivery(TimestampMixin, Base):
    """Notification queue with deduplication and delivery status."""

    __tablename__ = "alert_deliveries"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"))
    announcement_id: Mapped[int | None] = mapped_column(ForeignKey("announcements.id"))
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    change_type: Mapped[str] = mapped_column(Text, nullable=False)
    dedup_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    payload: Mapped[dict | None] = mapped_column(JSON, default=dict)
    # pending | sent | failed
    status: Mapped[str] = mapped_column(Text, default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    scheduled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class PollRun(Base):
    """Polling history and system health."""

    __tablename__ = "poll_runs"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # running | success | failed
    status: Mapped[str] = mapped_column(Text, default="running", nullable=False)
    items_seen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_new: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_changed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    alerts_queued: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[dict | None] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = [
    "Base",
    "Source",
    "Company",
    "Opportunity",
    "OpportunityVersion",
    "Announcement",
    "AnnouncementVersion",
    "Application",
    "UserPreference",
    "PortfolioItem",
    "ResearchReport",
    "ResumeSuggestion",
    "AlertDelivery",
    "PollRun",
]
