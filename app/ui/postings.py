"""Postings page: every tracked posting, with the same tags used in Telegram."""

from __future__ import annotations

import datetime as dt

import streamlit as st
from sqlalchemy import desc, select

from app.agent.research_agent import status_tag
from app.db import session_scope
from app.models import Opportunity
from app.ui.common import as_utc, days_until, fmt_date


def _application_status(record) -> str | None:
    raw = record.raw or {}
    return raw.get("application_status") or raw.get("job_profile_status")


def render() -> None:
    st.subheader("Postings")

    with st.sidebar:
        st.markdown("### Filters")
        search = st.text_input("Search", key="p_search", placeholder="Title or company")
        tag = st.selectbox(
            "Tag", ["All", "NEW", "APPLIED", "UPDATED", "TO APPLY", "OFFERED",
                    "REJECTED", "CLOSED", "OTHER"],
            key="p_tag",
        )
        only_deadline = st.checkbox("Only with a deadline", key="p_deadline")
        window = st.selectbox(
            "Detected", ["Any time", "Last 24 hours", "Last 7 days"], key="p_window"
        )

    now = dt.datetime.now(dt.timezone.utc)
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(Opportunity)
                .where(Opportunity.is_active.is_(True))
                .order_by(
                    desc(Opportunity.first_seen_at), desc(Opportunity.last_seen_at)
                )
                .limit(400)
            ).all()
        )

    if search:
        needle = search.strip().lower()
        rows = [
            r for r in rows
            if needle in (r.title or "").lower()
            or needle in (r.company_name or "").lower()
        ]
    if only_deadline:
        rows = [r for r in rows if r.deadline is not None]
    if window != "Any time":
        days = 1 if window == "Last 24 hours" else 7
        cutoff = now - dt.timedelta(days=days)
        rows = [r for r in rows if as_utc(r.first_seen_at) and as_utc(r.first_seen_at) >= cutoff]

    if tag != "All":
        keep = []
        for record in rows:
            _, label = status_tag(_application_status(record), None)
            if label.upper() == tag or (tag == "OTHER" and label == "ℹ️ UNKNOWN"):
                keep.append(record)
        rows = keep

    st.caption(f"{len(rows)} posting(s)")

    if not rows:
        st.info("Nothing matches these filters.")
        return

    for record in rows:
        status = _application_status(record)
        emoji, label = status_tag(status, None)
        days = days_until(record.deadline)
        header = f"{emoji} {label} · {record.title} — {record.company_name or 'Unknown'}"

        with st.expander(header):
            m1, m2, m3 = st.columns(3)
            with m1:
                st.write(f"**Type:** {record.work_type or 'unknown'}")
                st.write(f"**Location:** {record.location or 'unknown'}")
            with m2:
                st.write(f"**Compensation:** {record.compensation or 'unknown'}")
                st.write(
                    "**Eligibility:** "
                    + (record.eligibility or "unknown (not published)")
                )
            with m3:
                st.write(f"**Deadline:** {fmt_date(record.deadline)}")
                if days is not None:
                    st.write("⏰ " + ("passed" if days < 0 else f"{days} day(s) left"))
                st.write(f"**Portal status:** {status or 'unknown'}")

            st.caption(
                f"First seen {fmt_date(record.first_seen_at)} · "
                f"last checked {fmt_date(record.last_seen_at)}"
            )

            tabs = st.tabs(["Summary", "Job description"])
            with tabs[0]:
                if record.description:
                    st.write(record.description[:600])
                else:
                    st.caption("No JD text stored for this posting.")
            with tabs[1]:
                if record.description:
                    st.write(record.description[:6000])
                else:
                    st.caption(
                        "No JD published, or the detail has not been fetched yet."
                    )

            if record.apply_url:
                st.markdown(f"[Open in Superset]({record.apply_url})")