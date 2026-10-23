"""Overview: collector health, recent detections and upcoming deadlines."""

from __future__ import annotations

import datetime as dt

import streamlit as st
from sqlalchemy import desc, func, select

from app.db import session_scope
from app.models import AlertDelivery, Opportunity, PollRun
from app.ui.common import days_until, fmt_date, relative


def render() -> None:
    now = dt.datetime.now(dt.timezone.utc)

    with session_scope() as session:
        latest = session.scalar(select(PollRun).order_by(desc(PollRun.id)).limit(1))
        total = session.scalar(
            select(func.count(Opportunity.id)).where(Opportunity.is_active.is_(True))
        )
        detected_24h = session.scalar(
            select(func.count(Opportunity.id)).where(
                Opportunity.first_seen_at >= now - dt.timedelta(days=1)
            )
        )
        with_deadline = session.scalar(
            select(func.count(Opportunity.id)).where(
                Opportunity.deadline.is_not(None), Opportunity.deadline >= now
            )
        )
        pending = session.scalar(
            select(func.count(AlertDelivery.id)).where(AlertDelivery.status == "pending")
        )
        failed = session.scalar(
            select(func.count(AlertDelivery.id)).where(AlertDelivery.status == "failed")
        )
        recent = list(
            session.scalars(
                select(Opportunity)
                .where(Opportunity.first_seen_at.is_not(None))
                .order_by(desc(Opportunity.first_seen_at))
                .limit(8)
            ).all()
        )
        upcoming = list(
            session.scalars(
                select(Opportunity)
                .where(Opportunity.deadline.is_not(None))
                .where(Opportunity.deadline >= now)
                .order_by(Opportunity.deadline)
                .limit(8)
            ).all()
        )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Postings tracked", total or 0)
    with c2:
        st.metric("Detected in 24h", detected_24h or 0)
    with c3:
        st.metric("Open deadlines", with_deadline or 0)
    with c4:
        st.metric(
            "Alerts pending", pending or 0,
            delta="FAILED" if failed else None,
        )

    st.subheader("Collector health")
    if latest is None:
        st.warning("No poll has run yet — run `python -m app.main poll`.")
    else:
        s1, s2, s3, s4, s5 = st.columns(5)
        with s1:
            st.metric("Last run", latest.status)
            st.caption(relative(latest.finished_at))
        with s2:
            st.metric("Seen", latest.items_seen)
        with s3:
            st.metric("New", latest.items_new)
        with s4:
            st.metric("Changed", latest.items_changed)
        with s5:
            notes = latest.notes or {}
            st.metric("Documents", notes.get("items_documented", 0) or 0)

        if latest.error:
            st.error(latest.error)
        st.caption(
            "Started " + relative(latest.started_at)
            + " · alerts queued: " + str(latest.alerts_queued)
        )

    left, right = st.columns(2)

    with left:
        st.subheader("Recently detected")
        if not recent:
            st.caption("Nothing yet.")
        for item in recent:
            st.write(f"**{item.company_name or 'Unknown'}** — {item.title}")
            st.caption(f"First seen {relative(item.first_seen_at)}")

    with right:
        st.subheader("Closing soon")
        if not upcoming:
            st.success("No upcoming deadlines.")
        for item in upcoming:
            days = days_until(item.deadline)
            text = (
                f"**{item.company_name or 'Unknown'}** — {item.title} "
                f"· {days} day(s) left"
            )
            if days is not None and days <= 3:
                st.error(text)
            else:
                st.warning(text)