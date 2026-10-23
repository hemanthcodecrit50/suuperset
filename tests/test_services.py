"""Tests for preferences, portfolio and application tracking services."""

from __future__ import annotations

import pytest

from app.services.applications import STATUSES, find_opportunity, track
from app.services.portfolio import (
    add_item,
    delete_item,
    list_items,
    summary,
    update_item,
)
from app.services.preferences import (
    load_preferences,
    update_matching,
    update_notifications,
)


# --- preferences -------------------------------------------------------

def test_preferences_roundtrip(session):
    update_matching(session, skills=["python", "sql"], cgpa=8.5)
    prefs = load_preferences(session)
    assert prefs.matching["skills"] == ["python", "sql"]
    assert prefs.matching["cgpa"] == 8.5
    assert prefs.is_configured() is True


def test_preferences_merge_keeps_other_fields(session):
    update_matching(session, skills=["python"])
    update_matching(session, interests=["backend"])
    prefs = load_preferences(session)
    assert prefs.matching["skills"] == ["python"]
    assert prefs.matching["interests"] == ["backend"]


def test_min_alert_score_default_and_override(session):
    assert load_preferences(session).min_alert_score == 0.0
    update_notifications(session, min_alert_score=45)
    assert load_preferences(session).min_alert_score == 45.0


def test_reminder_days_parse(session):
    update_notifications(session, deadline_reminder_days=[3, 1])
    assert load_preferences(session).reminder_days == [3, 1]


def test_followed_companies_match(session):
    update_matching(session, companies=["Zensar", "Thoughtworks"])
    prefs = load_preferences(session)
    assert prefs.always_notify_company("zensar technologies") is True
    assert prefs.always_notify_company("THOUGHTWORKS") is True
    assert prefs.always_notify_company("Some Other Co") is False
    assert prefs.always_notify_company(None) is False


def test_custom_weights_are_used(session):
    update_matching(session, weights={"skills_alignment": 1.0})
    weights = load_preferences(session).weights()
    assert weights["skills_alignment"] == 1.0
    assert weights["role_alignment"] == 0.25  # untouched defaults remain


# --- portfolio ---------------------------------------------------------

def test_add_and_list_portfolio(session):
    item = add_item(
        session,
        name="Payments API",
        description="Django REST API",
        technologies="python, django, postgres",
        achievements="served 10k requests/day\ncut latency 40%",
        evidence_url="https://github.com/example/payments",
    )
    assert item.id is not None
    items = list_items(session)
    assert len(items) == 1
    assert items[0].technologies == ["python", "django", "postgres"]
    assert len(items[0].achievements) == 2


def test_portfolio_summary_collects_technologies(session):
    add_item(session, name="A", technologies="python, sql")
    add_item(session, name="B", technologies="sql, django")
    result = summary(session)
    assert set(result.technologies) == {"python", "sql", "django"}


def test_update_and_delete_portfolio(session):
    item = add_item(session, name="Old", technologies="python")
    update_item(session, item.id, name="New", status="completed")
    assert item.name == "New"
    assert item.status == "completed"
    assert delete_item(session, item.id) is True
    assert list_items(session) == []


def test_portfolio_rejects_bad_status(session):
    with pytest.raises(ValueError):
        add_item(session, name="X", status="nonsense")


# --- applications ------------------------------------------------------

def _seed_opportunity(session):
    from app.collector.change_detector import upsert_opportunity

    record, _ = upsert_opportunity(
        session,
        {"external_id": "o1", "title": "Backend Intern", "company_name": "Acme"},
    )
    return record


def test_track_creates_then_updates(session):
    opportunity = _seed_opportunity(session)
    first = track(session, opportunity, status="saved", next_action="Apply")
    assert first.created is True
    second = track(session, opportunity, status="applied", mark_applied=True)
    assert second.created is False
    assert second.application.status == "applied"
    assert second.application.applied_at is not None


def test_track_rejects_unknown_status(session):
    opportunity = _seed_opportunity(session)
    with pytest.raises(ValueError):
        track(session, opportunity, status="teleported")


def test_track_parses_due_date(session):
    opportunity = _seed_opportunity(session)
    result = track(session, opportunity, status="interview", next_action_at="2026-10-20")
    assert result.application.next_action_at is not None
    assert result.application.next_action_at.year == 2026


def test_find_opportunity_by_company(session):
    _seed_opportunity(session)
    assert find_opportunity(session, "Acme") is not None
    assert find_opportunity(session, "Nope") is None


def test_every_status_is_lowercase_and_usable(session):
    opportunity = _seed_opportunity(session)
    for status in STATUSES:
        result = track(session, opportunity, status=status)
        assert result.application.status == status.lower()