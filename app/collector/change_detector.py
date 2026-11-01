"""Change detection, idempotent upserts and version history.

Stable identifiers (``external_id``) plus content hashes let us distinguish a
genuinely new record from a changed one and from an unchanged re-poll.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AlertDelivery,
    Announcement,
    AnnouncementVersion,
    Opportunity,
    OpportunityVersion,
)

logger = logging.getLogger(__name__)

# Fields whose change should count as a meaningful opportunity change.
OPPORTUNITY_HASH_FIELDS = (
    "title",
    "company_name",
    "location",
    "compensation",
    "eligibility",
    "deadline",
    "description",
    "skills",
)

ANNOUNCEMENT_HASH_FIELDS = ("title", "body", "category", "published_at")

NEW = "new"
CHANGED = "changed"
UNCHANGED = "unchanged"


def _normalize(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.astimezone(dt.UTC).isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {k: _normalize(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    return value


def canonical_hash(data: Mapping[str, Any] | Iterable[str], fields: Iterable[str] | None = None) -> str:
    """Return a stable SHA-256 hash for the selected fields of ``data``.

    Whitespace-only differences are ignored so cosmetic re-renders do not look
    like real changes.
    """
    if fields is None:
        fields = list(data.keys())  # type: ignore[union-attr]
    payload = {}
    for field_name in fields:  # type: ignore[union-attr]
        value = data.get(field_name)  # type: ignore[union-attr]
        if isinstance(value, str):
            value = " ".join(value.split())
        payload[field_name] = _normalize(value)
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def diff_fields(
    old: Mapping[str, Any], new: Mapping[str, Any], fields: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """Return ``{field: {"old": ..., "new": ...}}`` for changed fields."""
    changes: dict[str, dict[str, Any]] = {}
    for field_name in fields:
        old_value = old.get(field_name)
        new_value = new.get(field_name)
        if _normalize(old_value) != _normalize(new_value):
            changes[field_name] = {"old": old_value, "new": new_value}
    return changes


def _get_or_create_source_id(session: Session) -> int | None:
    from app.models import Source

    source = session.scalar(select(Source).order_by(Source.id))
    return source.id if source else None


def upsert_opportunity(
    session: Session, data: Mapping[str, Any], source_id: int | None = None
) -> tuple[Opportunity, str]:
    """Insert or update an opportunity; return ``(obj, change_type)``.

    ``change_type`` is one of ``"new"``, ``"changed"`` or ``"unchanged"``.
    Historical versions are written on every change.
    """
    now = dt.datetime.now(dt.UTC)
    source_id = source_id if source_id is not None else _get_or_create_source_id(session)
    external_id = str(data["external_id"])

    existing = session.scalar(
        select(Opportunity).where(
            Opportunity.external_id == external_id,
            Opportunity.source_id == source_id,
        )
    )

    values = {
        "source_id": source_id,
        "external_id": external_id,
        "company_name": data.get("company_name"),
        "title": data.get("title") or "(untitled role)",
        "role": data.get("role"),
        "location": data.get("location"),
        "job_function": data.get("job_function"),
        "work_type": data.get("work_type"),
        "compensation": data.get("compensation"),
        "eligibility": data.get("eligibility"),
        "skills": data.get("skills") or [],
        "deadline": data.get("deadline"),
        "apply_url": data.get("apply_url"),
        "description": data.get("description"),
        "attachments": data.get("attachments") or [],
        "raw": data.get("raw") or {},
    }
    new_hash = canonical_hash(values, OPPORTUNITY_HASH_FIELDS)

    if existing is None:
        record = Opportunity(
            **values,
            content_hash=new_hash,
            first_seen_at=now,
            last_seen_at=now,
            is_active=True,
        )
        session.add(record)
        session.flush()
        return record, NEW

    old_hash = existing.content_hash
    existing.last_seen_at = now
    existing.is_active = True
    # Portal status lives in raw and is not hashed, so refresh it on every poll;
    # merging keeps what earlier polls and the agent stored (parsed JD, documents)
    merged_raw = {**(existing.raw or {}), **values["raw"]}
    values["raw"] = merged_raw
    if values["attachments"] == [] and existing.attachments:
        values["attachments"] = existing.attachments
    if merged_raw != existing.raw:
        existing.raw = merged_raw
    if values["attachments"] != existing.attachments:
        existing.attachments = values["attachments"]

    if old_hash == new_hash:
        session.flush()
        return existing, UNCHANGED

    session.add(
        OpportunityVersion(
            opportunity_id=existing.id,
            content_hash=old_hash or "",
            title=existing.title,
            description=existing.description,
            eligibility=existing.eligibility,
            deadline=existing.deadline,
            skills=existing.skills,
            raw=existing.raw,
        )
    )

    for key, value in values.items():
        setattr(existing, key, value)
    existing.content_hash = new_hash
    session.flush()
    return existing, CHANGED


def upsert_announcement(
    session: Session, data: Mapping[str, Any], source_id: int | None = None
) -> tuple[Announcement, str]:
    """Insert or update an announcement; return ``(obj, change_type)``."""
    now = dt.datetime.now(dt.UTC)
    source_id = source_id if source_id is not None else _get_or_create_source_id(session)
    external_id = str(data["external_id"])

    existing = session.scalar(
        select(Announcement).where(
            Announcement.external_id == external_id,
            Announcement.source_id == source_id,
        )
    )

    values = {
        "source_id": source_id,
        "external_id": external_id,
        "title": data.get("title") or "(untitled announcement)",
        "body": data.get("body"),
        "url": data.get("url"),
        "category": data.get("category"),
        "published_at": data.get("published_at"),
        "raw": data.get("raw") or {},
    }
    new_hash = canonical_hash(values, ANNOUNCEMENT_HASH_FIELDS)

    if existing is None:
        record = Announcement(
            **values,
            content_hash=new_hash,
            first_seen_at=now,
            last_seen_at=now,
            is_active=True,
        )
        session.add(record)
        session.flush()
        return record, NEW

    old_hash = existing.content_hash
    existing.last_seen_at = now
    existing.is_active = True

    if old_hash == new_hash:
        return existing, UNCHANGED

    session.add(
        AnnouncementVersion(
            announcement_id=existing.id,
            content_hash=old_hash or "",
            title=existing.title,
            body=existing.body,
            raw=existing.raw,
        )
    )
    # Keep what was stored before, but read the schedule again from the new text
    merged_raw = {**(existing.raw or {}), **values["raw"]}
    merged_raw.pop("schedule_checked", None)
    values["raw"] = merged_raw
    for key, value in values.items():
        setattr(existing, key, value)
    existing.content_hash = new_hash
    session.flush()
    return existing, CHANGED


def enqueue_alert(
    session: Session,
    *,
    channel: str,
    change_type: str,
    dedup_key: str,
    payload: Mapping[str, Any],
    opportunity_id: int | None = None,
    announcement_id: int | None = None,
    scheduled_at: dt.datetime | None = None,
) -> AlertDelivery | None:
    """Queue an alert unless an identical one already exists.

    Deduplication is by ``dedup_key`` (a unique constraint backs this up), so a
    repeated poll never floods the user with duplicate messages.
    """
    existing = session.scalar(
        select(AlertDelivery).where(AlertDelivery.dedup_key == dedup_key)
    )
    if existing is not None:
        logger.debug("Alert already queued (dedup_key=%s)", dedup_key)
        return None

    delivery = AlertDelivery(
        opportunity_id=opportunity_id,
        announcement_id=announcement_id,
        channel=channel,
        change_type=change_type,
        dedup_key=dedup_key,
        payload=dict(payload),
        status="pending",
        scheduled_at=scheduled_at,
    )
    session.add(delivery)
    session.flush()
    return delivery
