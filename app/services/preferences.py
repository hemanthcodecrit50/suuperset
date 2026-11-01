"""User preferences: interests, hard constraints and alert thresholds.

Stored as key/value rows in ``user_preferences`` so new settings never require
a schema change. Everything here is optional: with no preferences the system
still works and simply reports "unknown" rather than guessing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import UserPreference

logger = logging.getLogger(__name__)

KEY_MATCHING = "matching"
KEY_NOTIFICATIONS = "notifications"
KEY_PROFILE_INSIGHT = "profile_insight"

# Fields the user can set under `matching`.
MATCHING_FIELDS = (
    "skills",
    "interests",
    "technologies",
    "work_types",
    "locations",
    "companies",
    "min_compensation",
    "degree",
    "branch",
    "cgpa",
    "graduation_year",
)

DEFAULT_WEIGHTS = {
    "skills_alignment": 0.35,
    "role_alignment": 0.25,
    "compensation_location": 0.20,
    "tech_worktype": 0.20,
}


@dataclass
class Preferences:
    """Merged view of stored preferences."""

    matching: dict[str, Any] = field(default_factory=dict)
    notifications: dict[str, Any] = field(default_factory=dict)
    # What the AI understood about the profile; the student can remove any part of it
    insight: dict[str, Any] = field(default_factory=dict)

    # -- convenience accessors used by poll / reminders ----------------
    @property
    def min_alert_score(self) -> float:
        """Minimum relevance score required to send an alert."""
        value = self.notifications.get("min_alert_score", 0)
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @property
    def always_alert_changes(self) -> bool:
        """Alert on edits to jobs the user already tracks."""
        return bool(self.notifications.get("always_alert_changes", True))

    @property
    def reminder_days(self) -> list[int]:
        raw = self.notifications.get("deadline_reminder_days", [3, 1])
        if isinstance(raw, str):
            raw = [part for part in raw.replace(",", " ").split() if part]
        days: list[int] = []
        for value in raw or []:
            try:
                days.append(int(value))
            except (TypeError, ValueError):
                continue
        return days or [3, 1]

    @property
    def digest_enabled(self) -> bool:
        return bool(self.notifications.get("daily_digest", True))

    @property
    def followed_companies(self) -> list[str]:
        raw = self.matching.get("companies") or []
        if isinstance(raw, str):
            raw = [raw]
        return [str(c).strip().lower() for c in raw if str(c).strip()]

    def always_notify_company(self, company_name: str | None) -> bool:
        """True when the user explicitly follows this company."""
        if not company_name:
            return False
        needle = company_name.strip().lower()
        return any(
            needle in followed or followed in needle
            for followed in self.followed_companies
        )

    def weights(self) -> dict[str, float]:
        configured = self.matching.get("weights") or {}
        merged = {**DEFAULT_WEIGHTS, **configured}
        return {key: float(value) for key, value in merged.items()}

    def as_match_kwargs(self) -> dict[str, Any]:
        """Shape accepted by :func:`app.services.matching.score_opportunity`."""
        payload = dict(self.matching)
        payload.setdefault("weights", self.weights())
        payload["insight"] = active_insight(self.insight)
        return payload

    def is_configured(self) -> bool:
        return any(self.matching.get(k) for k in MATCHING_FIELDS)


def _read(session: Session, key: str) -> dict[str, Any]:
    row = session.scalar(select(UserPreference).where(UserPreference.key == key))
    value = getattr(row, "value", None) if row else None
    return dict(value) if isinstance(value, dict) else {}


def load_preferences(session: Session) -> Preferences:
    return Preferences(
        matching=_read(session, KEY_MATCHING),
        notifications=_read(session, KEY_NOTIFICATIONS),
        insight=_read(session, KEY_PROFILE_INSIGHT),
    )


def load_setting(session: Session, key: str) -> dict[str, Any]:
    """One stored settings blob, or an empty dict."""
    return _read(session, key)


def save_setting(session: Session, key: str, value: dict[str, Any]) -> None:
    """Replace one stored settings blob."""
    _write(session, key, value)


def active_insight(insight: dict[str, Any]) -> dict[str, Any]:
    """The profile insight minus everything the student chose to remove."""
    removed = set(insight.get("removed") or [])
    if not insight:
        return {}
    return {
        "implied_skills": {
            skill: [item for item in implied if f"skill:{item}" not in removed]
            for skill, implied in (insight.get("implied_skills") or {}).items()
        },
        "places": {
            place: [city for city in cities if f"place:{city}" not in removed]
            for place, cities in (insight.get("places") or {}).items()
        },
        "anywhere": bool(insight.get("anywhere")) and "anywhere" not in removed,
        "role_fits": [
            role
            for role in insight.get("role_fits") or []
            if f"role:{role}" not in removed
        ],
        "role_misfits": [
            role
            for role in insight.get("role_misfits") or []
            if f"misfit:{role}" not in removed
        ],
    }


def _write(session: Session, key: str, value: dict[str, Any]) -> None:
    row = session.scalar(select(UserPreference).where(UserPreference.key == key))
    if row is None:
        row = UserPreference(key=key, value=value)
        session.add(row)
    else:
        row.value = value
    session.flush()


def update_matching(
    session: Session,
    *,
    skills: list[str] | None = None,
    interests: list[str] | None = None,
    technologies: list[str] | None = None,
    work_types: list[str] | None = None,
    locations: list[str] | None = None,
    companies: list[str] | None = None,
    min_compensation: float | None = None,
    degree: str | None = None,
    branch: str | None = None,
    cgpa: float | None = None,
    graduation_year: int | None = None,
    weights: dict[str, float] | None = None,
    clear: bool = False,
) -> dict[str, Any]:
    """Merge values into the ``matching`` preference blob."""
    current = {} if clear else _read(session, KEY_MATCHING)

    mapping = {
        "skills": skills,
        "interests": interests,
        "technologies": technologies,
        "work_types": work_types,
        "locations": locations,
        "companies": companies,
        "min_compensation": min_compensation,
        "degree": degree,
        "branch": branch,
        "cgpa": cgpa,
        "graduation_year": graduation_year,
        "weights": weights,
    }
    for field_name, value in mapping.items():
        if value is not None:
            current[field_name] = value

    _write(session, KEY_MATCHING, current)
    return current


def replace_profile(session: Session, values: dict[str, Any]) -> dict[str, Any]:
    """Save the profile form as given: a field left blank is cleared, not kept."""
    current = _read(session, KEY_MATCHING)
    for field_name, value in values.items():
        if value is None:
            current.pop(field_name, None)
        else:
            current[field_name] = value
    _write(session, KEY_MATCHING, current)
    return current


def update_notifications(
    session: Session,
    *,
    min_alert_score: float | None = None,
    always_alert_changes: bool | None = None,
    deadline_reminder_days: list[int] | None = None,
    daily_digest: bool | None = None,
    digest_hour: int | None = None,
    clear: bool = False,
) -> dict[str, Any]:
    """Merge values into the ``notifications`` preference blob."""
    current = {} if clear else _read(session, KEY_NOTIFICATIONS)

    mapping = {
        "min_alert_score": min_alert_score,
        "always_alert_changes": always_alert_changes,
        "deadline_reminder_days": deadline_reminder_days,
        "daily_digest": daily_digest,
        "digest_hour": digest_hour,
    }
    for field_name, value in mapping.items():
        if value is not None:
            current[field_name] = value

    _write(session, KEY_NOTIFICATIONS, current)
    return current
