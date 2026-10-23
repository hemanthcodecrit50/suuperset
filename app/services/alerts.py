"""Alert delivery: Telegram, email, retries and deduplication.

The queue lives in the ``alert_deliveries`` table. Each row is a single
notification for a single channel, deduplicated by ``dedup_key``. This module
drains the queue with bounded retries and records failures.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import AlertDelivery

logger = logging.getLogger(__name__)

CHANNEL_TELEGRAM = "telegram"
CHANNEL_EMAIL = "email"
CHANNEL_DASHBOARD = "dashboard"


@dataclass
class DeliveryResult:
    delivery_id: int
    channel: str
    ok: bool
    error: str | None = None


def escape_markdown(text: str) -> str:
    """Escape characters Telegram's Markdown parser treats as formatting.

    Job titles and company names routinely contain ``_``, ``*``, ``.`` etc.
    (e.g. "SDE_1 (IT+PBC)"), which otherwise cause a 400 from the API.
    """
    for char in ("_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+",
                 "-", "=", "|", "{", "}", ".", "!"):
        text = text.replace(char, f"\\{char}")
    return text


def format_alert_message(payload: dict) -> str:
    """Render an alert payload into a readable multi-line message.

    ``message`` short-circuits the standard field list (used by the research
    agent, which pre-renders the whole thing), as does ``digest_body``.
    """
    pre_rendered = payload.get("message") or payload.get("digest_body")
    if pre_rendered:
        return str(pre_rendered)

    lines: list[str] = []
    title = payload.get("title") or "New opportunity"
    lines.append(f"*{escape_markdown(str(title))}*")

    for label, key in (
        ("Company", "company_name"),
        ("Location", "location"),
        ("Compensation", "compensation"),
        ("Deadline", "deadline"),
        ("Change", "change_type"),
        ("Why it matters", "relevance_reason"),
        ("Next action", "next_action"),
    ):
        value = payload.get(key)
        if value:
            lines.append(f"{label}: {escape_markdown(str(value))}")

    url = payload.get("url")
    if url:
        lines.append(str(url))
    return "\n".join(lines)


def deliver_pending(session: Session, settings: Settings | None = None) -> list[DeliveryResult]:
    """Attempt to deliver all pending alerts.

    Rows that succeed are marked ``sent``; failures are retried up to
    ``alert_max_attempts`` before being marked ``failed``.
    """
    settings = settings or get_settings()
    now = dt.datetime.now(dt.timezone.utc)

    pending = session.scalars(
        select(AlertDelivery)
        .where(AlertDelivery.status == "pending")
        .where(
            (AlertDelivery.scheduled_at.is_(None))
            | (AlertDelivery.scheduled_at <= now)
        )
        .order_by(AlertDelivery.id)
    ).all()

    results: list[DeliveryResult] = []
    for delivery in pending:
        delivery.attempts += 1
        try:
            _dispatch(delivery, settings)
            delivery.status = "sent"
            delivery.sent_at = now
            delivery.last_error = None
            results.append(DeliveryResult(delivery.id, delivery.channel, True))
        except Exception as exc:  # noqa: BLE001 - record any delivery failure
            delivery.last_error = str(exc)[:2000]
            if delivery.attempts >= settings.alert_max_attempts:
                delivery.status = "failed"
            logger.warning(
                "Alert %s (%s) attempt %d failed: %s",
                delivery.id,
                delivery.channel,
                delivery.attempts,
                exc,
            )
            results.append(DeliveryResult(delivery.id, delivery.channel, False, str(exc)))
    session.flush()
    return results


def _dispatch(delivery: AlertDelivery, settings: Settings) -> None:
    payload = delivery.payload or {}
    if delivery.channel == CHANNEL_TELEGRAM:
        send_telegram(format_alert_message(payload), settings)
    elif delivery.channel == CHANNEL_EMAIL:
        send_email(
            subject=payload.get("title", "Placement update"),
            body=format_alert_message(payload),
            settings=settings,
        )
    elif delivery.channel == CHANNEL_DASHBOARD:
        # Dashboard reads the row directly; nothing to send.
        return
    else:
        raise ValueError(f"Unknown alert channel: {delivery.channel}")


def send_telegram(message: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if not settings.telegram_configured:
        raise RuntimeError("Telegram is not configured (missing token/chat id).")

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    response = httpx.post(
        url,
        json={
            "chat_id": settings.telegram_chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        },
        timeout=15.0,
    )
    if response.status_code != 200:
        # Include the API's explanation; a bare status code is hard to debug.
        raise RuntimeError(
            f"Telegram API {response.status_code}: {response.text[:300]}"
        )


def send_email(subject: str, body: str, settings: Settings | None = None) -> None:
    import smtplib
    from email.message import EmailMessage

    settings = settings or get_settings()
    if not settings.email_configured:
        raise RuntimeError("Email is not configured (missing SMTP settings).")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from
    message["To"] = settings.alert_email_to
    message.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
        if settings.smtp_use_tls:
            server.starttls()
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(message)
