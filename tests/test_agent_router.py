"""Tests for the router node."""

from __future__ import annotations

import pytest
from agent_helpers import LONG_PAST, make_memory, make_opportunity

from app.agent.router import route_change
from app.agent.state import Route


def _route(change_type="new", changes=None, memory=None, **opportunity_overrides):
    return route_change(
        make_opportunity(**opportunity_overrides),
        change_type,
        changes or {},
        memory or make_memory(),
    ).route


def test_new_posting_takes_the_full_path():
    assert _route() is Route.FULL


def test_new_posting_past_its_deadline_is_dropped():
    assert _route(deadline=LONG_PAST) is Route.DROP


@pytest.mark.parametrize("status", ["REJECTED", "WITHDRAWN", "CLOSED_FOR_APPLICATIONS"])
def test_posting_the_student_is_out_of_is_dropped(status):
    assert _route(application_status=status) is Route.DROP


def test_new_posting_the_student_is_not_eligible_for_takes_the_short_path():
    assert _route(eligible=False) is Route.SHORT


def test_change_to_an_applied_posting_takes_the_short_path():
    changes = {"deadline": {"old": None, "new": "2999-01-02"}}
    assert _route("changed", changes, application_status="APPLIED") is Route.SHORT


def test_change_to_a_posting_at_an_applied_company_takes_the_short_path():
    memory = make_memory(is_company_applied=True)
    changes = {"description": {"old": "a", "new": "b"}}
    assert _route("changed", changes, memory) is Route.SHORT


def test_applied_posting_stays_routable_after_its_deadline():
    changes = {"description": {"old": "a", "new": "b"}}
    route = _route("changed", changes, deadline=LONG_PAST, application_status="APPLIED")
    assert route is Route.SHORT


def test_change_touching_no_tracked_field_is_dropped():
    assert _route("changed", {}) is Route.DROP


def test_change_to_the_description_takes_the_full_path():
    changes = {"description": {"old": "a", "new": "b"}}
    assert _route("changed", changes) is Route.FULL


def test_title_only_change_takes_the_short_path():
    changes = {"title": {"old": "a", "new": "b"}}
    assert _route("changed", changes) is Route.SHORT
