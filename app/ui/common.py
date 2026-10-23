"""Shared helpers for the dashboard pages."""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session


def as_utc(value: dt.datetime | None) -> dt.datetime | None:
    """SQLite returns naive datetimes; PostgreSQL returns aware ones."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def fmt_date(value: dt.datetime | None) -> str:
    value = as_utc(value)
    return value.strftime("%Y-%m-%d") if value else "unknown"


def fmt_datetime(value: dt.datetime | None) -> str:
    value = as_utc(value)
    return value.strftime("%Y-%m-%d %H:%M") if value else "unknown"


def days_until(value: dt.datetime | None) -> int | None:
    value = as_utc(value)
    if value is None:
        return None
    return (value - dt.datetime.now(dt.timezone.utc)).days


def relative(value: dt.datetime | None) -> str:
    """Human-friendly '3 hours ago' style string."""
    value = as_utc(value)
    if value is None:
        return "never"
    seconds = (dt.datetime.now(dt.timezone.utc) - value).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def commit_and_rerun(session: Session, message: str) -> None:
    import streamlit as st

    session.commit()
    st.success(message)
    st.rerun()


def list_to_text(value) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value
    return ", ".join(str(v) for v in value)