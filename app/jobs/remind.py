"""Deadline reminders.

Finds active opportunities whose deadline falls inside the configured lead
times and queues a reminder, deduplicated per (opportunity, deadline). Safe to
run frequently — it only queues once per deadline.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import select

from app.agent.research_agent import format_telegram
from app.collector.change_detector import enqueue_alert
from app.db import session_scope
from app.models import Opportunity
from app.services.alerts import CHANNEL_EMAIL, CHANNEL_TELEGRAM
from app.services.preferences import load_preferences

logger = logging.getLogger(__name__)


@dataclass
class ReminderResult:
    checked: int = 0
    reminded: int = 0


def _channel(preferences, settings) -> str:
    if settings.telegram_configured:
        return CHANNEL_TELEGRAM
    if settings.email_configured:
        return CHANNEL_EMAIL
    return "dashboard"


def run_reminders(now: dt.datetime | None = None, max_days: int = 30) -> ReminderResult:
    """Queue reminders for deadlines approaching in the configured windows."""
    from app.config import get_settings

    settings = get_settings()
    now = now or dt.datetime.now(dt.timezone.utc)
    result = ReminderResult()

    with session_scope() as session:
        preferences = load_preferences(session)
        reminder_days = preferences.reminder_days
        horizon = now + dt.timedelta(days=max(max_days, max(reminder_days or [1])))
        channel = _channel(preferences, settings)

        candidates = session.scalars(
            select(Opportunity).where(
                Opportunity.is_active.is_(True),
                Opportunity.deadline.is_not(None),
                Opportunity.deadline >= now,
                Opportunity.deadline <= horizon,
            )
        ).all()

        for opportunity in candidates:
            result.checked += 1
            deadline = opportunity.deadline
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=dt.timezone.utc)
            days_left = (deadline - now).days

            due_window = any(
                0 <= days_left <= window for window in reminder_days
            )
            # Already nudged for this exact deadline?
            if opportunity.deadline_reminded_at is not None:
                already = opportunity.deadline_reminded_at
                if already.tzinfo is None:
                    already = already.replace(tzinfo=dt.timezone.utc)
                if already >= deadline - dt.timedelta(days=max(reminder_days or [1])):
                    continue

            if not due_window:
                continue

            queued = enqueue_alert(
                session,
                channel=channel,
                change_type="deadline_reminder",
                dedup_key=(
                    f"deadline:{opportunity.id}:"
                    f"{deadline.date().isoformat()}:{days_left}"
                ),
                opportunity_id=opportunity.id,
                payload={
                    # Same renderer as new/changed postings, so the tag and
                    # layout stay consistent across every alert.
                    "message": format_telegram(
                        opportunity,
                        change_type=None,
                        now=now,
                        reminder_days_left=days_left,
                    ),
                    "title": opportunity.title,
                    "change_type": "deadline_reminder",
                    "url": opportunity.apply_url,
                },
            )
            if queued is not None:
                result.reminded += 1
                opportunity.deadline_reminded_at = now

    if result.reminded:
        logger.info("Queued %d deadline reminder(s).", result.reminded)
    return result


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    result = run_reminders()
    logger.info(
        "Deadline reminders: checked=%d reminded=%d", result.checked, result.reminded
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())