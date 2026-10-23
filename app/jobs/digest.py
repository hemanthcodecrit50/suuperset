"""Daily digest.

Sends one summary per day at the configured hour: what is new, what is closing
soon, and what the user is actively tracking. Deduplicated per calendar day so
repeated runs do not spam.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import select

from app.collector.change_detector import enqueue_alert
from app.db import session_scope
from app.models import Application, Opportunity
from app.services.alerts import CHANNEL_EMAIL, CHANNEL_TELEGRAM
from app.services.preferences import load_preferences

logger = logging.getLogger(__name__)

DIGEST_WINDOW_DAYS = 7


@dataclass
class DigestResult:
    sent: bool = False
    reason: str = ""
    new_count: int = 0
    closing_count: int = 0


def _channel(settings) -> str:
    if settings.telegram_configured:
        return CHANNEL_TELEGRAM
    if settings.email_configured:
        return CHANNEL_EMAIL
    return "dashboard"


def build_digest_text(
    new_items: list[Opportunity], closing: list[Opportunity], applications: list
) -> str:
    lines = ["*Daily placement digest*"]

    if new_items:
        lines.append(f"\nNew since yesterday ({len(new_items)}):")
        for item in new_items[:10]:
            score = f" [{item.relevance_score:.0f}]" if item.relevance_score else ""
            lines.append(f"- {item.title} - {item.company_name or 'unknown'}{score}")
        if len(new_items) > 10:
            lines.append(f"...and {len(new_items) - 10} more")
    else:
        lines.append("\nNo new postings since yesterday.")

    if closing:
        lines.append(f"\nClosing within {DIGEST_WINDOW_DAYS} days ({len(closing)}):")
        for item in closing[:10]:
            deadline = item.deadline
            if deadline is not None and deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=dt.timezone.utc)
            when = deadline.date().isoformat() if deadline else "unknown"
            lines.append(f"- {item.title} - {item.company_name or 'unknown'} ({when})")
    else:
        lines.append("\nNo deadlines in the next week.")

    if applications:
        lines.append("\nIn progress:")
        for application, opportunity in applications[:10]:
            lines.append(f"- [{application.status}] {opportunity.title}")

    lines.append("\nReview everything in the dashboard.")
    return "\n".join(lines)


def run_digest(force: bool = False, now: dt.datetime | None = None) -> DigestResult:
    """Queue today's digest, unless disabled, outside the hour, or already sent."""
    from app.config import get_settings

    settings = get_settings()
    now = now or dt.datetime.now(dt.timezone.utc)

    with session_scope() as session:
        preferences = load_preferences(session)
        if not preferences.digest_enabled:
            return DigestResult(reason="digest disabled in preferences")

        hour = int(preferences.notifications.get("digest_hour", settings.alert_digest_hour))
        if not force and now.hour != hour:
            return DigestResult(reason=f"not digest hour (now {now.hour:02d}, want {hour:02d})")

        today = now.date().isoformat()
        dedup_key = f"digest:{today}"
        from app.models import AlertDelivery

        already = session.scalar(
            select(AlertDelivery).where(AlertDelivery.dedup_key == dedup_key)
        )
        if already is not None and not force:
            return DigestResult(reason=f"already sent today ({today})")

        since = now - dt.timedelta(days=1)
        new_items = list(
            session.scalars(
                select(Opportunity)
                .where(Opportunity.first_seen_at.is_not(None))
                .where(Opportunity.first_seen_at >= since)
                .order_by(Opportunity.relevance_score.desc().nullslast())
            ).all()
        )
        horizon = now + dt.timedelta(days=DIGEST_WINDOW_DAYS)
        closing = list(
            session.scalars(
                select(Opportunity)
                .where(Opportunity.deadline.is_not(None))
                .where(Opportunity.deadline >= now)
                .where(Opportunity.deadline <= horizon)
                .order_by(Opportunity.deadline)
            ).all()
        )
        applications = [
            (application, opportunity)
            for application, opportunity in session.execute(
                select(Application, Opportunity).join(
                    Opportunity, Application.opportunity_id == Opportunity.id
                )
            ).all()
        ]

        text = build_digest_text(new_items, closing, applications)
        queued = enqueue_alert(
            session,
            channel=_channel(settings),
            change_type="daily_digest",
            dedup_key=dedup_key,
            payload={
                "title": "Daily placement digest",
                "change_type": "daily_digest",
                "relevance_reason": (
                    f"{len(new_items)} new, {len(closing)} closing, "
                    f"{len(applications)} tracked"
                ),
                "next_action": "Open the dashboard to review",
                "digest_body": text,
            },
        )
        if queued is None and not force:
            return DigestResult(reason=f"already sent today ({today})")

        return DigestResult(
            sent=True,
            new_count=len(new_items),
            closing_count=len(closing),
        )


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    result = run_digest()
    logger.info("Daily digest: sent=%s reason=%s", result.sent, result.reason or "ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())