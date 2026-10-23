"""Alerts page: what is queued, what was sent, and send it now."""

from __future__ import annotations

import streamlit as st
from sqlalchemy import desc, select

from app.config import get_settings
from app.db import session_scope
from app.models import AlertDelivery
from app.services.alerts import deliver_pending
from app.ui.common import fmt_datetime


def render() -> None:
    st.subheader("Alert queue")

    settings = get_settings()
    if settings.telegram_configured:
        st.caption(f"Telegram ready · chat {settings.telegram_chat_id}")
    else:
        st.warning(
            "Telegram is not configured — alerts stay queued and never send. "
            "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env."
        )

    if st.button("Send pending alerts now", type="primary"):
        with session_scope() as session:
            results = deliver_pending(session)
        sent = sum(1 for r in results if r.ok)
        failed = len(results) - sent
        st.success(f"Delivered {sent}/{len(results)}.")
        if failed:
            st.error(f"{failed} failed — see the error column below.")
        st.rerun()

    with session_scope() as session:
        rows = list(
            session.scalars(
                select(AlertDelivery).order_by(desc(AlertDelivery.id)).limit(300)
            ).all()
        )

    if not rows:
        st.info("The queue is empty.")
        return

    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    st.caption(" · ".join(f"{name}: {counts[name]}" for name in sorted(counts)))

    preview = st.multiselect(
        "Show status",
        options=sorted(counts),
        default=sorted(counts),
        key="a_status",
    )

    table = [
        {
            "ID": row.id,
            "Type": row.change_type,
            "Channel": row.channel,
            "Status": row.status,
            "Attempts": row.attempts,
            "Sent": fmt_datetime(row.sent_at),
            "Error": (row.last_error or "")[:60],
        }
        for row in rows
        if row.status in preview
    ]
    st.dataframe(table, width="stretch", hide_index=True)

    with st.expander("Message preview"):
        for row in rows[:20]:
            if row.status not in preview:
                continue
            payload = row.payload or {}
            message = payload.get("message") or payload.get("digest_body")
            if message:
                st.markdown(f"**#{row.id} · {row.change_type}**")
                st.code(message, language=None)