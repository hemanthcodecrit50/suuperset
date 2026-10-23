"""Streamlit dashboard for change detection, reminders and research documents.

Run with::

    streamlit run app/dashboard.py

Pages mirror the pipeline exactly:
  Overview  - collector health, recent detections, closing deadlines
  Postings  - every tracked posting with the same tags used in Telegram
  Documents - the markdown files written by the research agent
  Alerts    - the queue, with a manual send button
"""

from __future__ import annotations

from sqlalchemy import desc, select

import streamlit as st

from app.config import get_settings
from app.db import session_scope
from app.models import PollRun
from app.ui import alerts, documents, overview, postings
from app.ui.common import relative

st.set_page_config(
    page_title="Placement Intelligence",
    page_icon="\U0001F4BC",
    layout="wide",
)

PAGES = {
    "Overview": overview.render,
    "Postings": postings.render,
    "Documents": documents.render,
    "Alerts": alerts.render,
}


def _sidebar() -> str:
    settings = get_settings()
    with st.sidebar:
        st.markdown("### Status")
        with session_scope() as session:
            latest = session.scalar(select(PollRun).order_by(desc(PollRun.id)).limit(1))

        if latest is None:
            st.caption("No poll has run yet")
        else:
            st.caption(
                f"Last poll: {latest.status} · {relative(latest.finished_at)}"
            )

        st.caption(
            "Telegram: "
            + ("ready" if settings.telegram_configured else "not configured")
        )
        st.caption(
            f"Collector: {settings.collector_mode} · details: "
            f"{'on' if settings.fetch_job_details else 'off'}"
        )

        st.markdown("---")
        choice = st.radio("Navigate", list(PAGES), label_visibility="collapsed")
        st.caption(
            "Session expires periodically. If collection stops, re-run "
            "`python -m app.main login`."
        )
    return choice


def main() -> None:
    page = _sidebar()
    PAGES[page]()


if __name__ == "__main__":
    main()