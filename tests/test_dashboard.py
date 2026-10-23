"""Tests for dashboard helpers (no Streamlit runtime required)."""

from __future__ import annotations

import datetime as dt

import pytest

from app.ui.common import as_utc


def test_naive_datetime_is_assumed_utc():
    naive = dt.datetime(2026, 1, 1, 12, 0, 0)
    aware = as_utc(naive)
    assert aware.tzinfo is dt.timezone.utc
    assert aware.hour == 12


def test_aware_datetime_is_unchanged():
    aware = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
    assert as_utc(aware) == aware


def test_none_passes_through():
    assert as_utc(None) is None


def test_comparison_no_longer_raises():
    """The bug: SQLite returns naive datetimes, breaking `deadline >= now`."""
    naive = dt.datetime(2026, 1, 1, 12, 0, 0)
    now = dt.datetime.now(dt.timezone.utc)
    # Without normalisation this raises TypeError.
    assert (as_utc(naive) >= now) is False


