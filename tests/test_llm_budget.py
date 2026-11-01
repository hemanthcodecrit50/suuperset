"""Tests for the shared daily model request budget."""

from __future__ import annotations

import json

from app.services.llm_budget import LlmBudget, _quota_day


def test_requests_are_allowed_until_the_cap(tmp_path):
    budget = LlmBudget(tmp_path / "usage.json", daily_limit=2, min_interval_seconds=0)
    assert [budget.acquire(), budget.acquire(), budget.acquire()] == [True, True, False]


def test_usage_reports_requests_spent_today(tmp_path):
    budget = LlmBudget(tmp_path / "usage.json", daily_limit=5, min_interval_seconds=0)
    budget.acquire()
    assert budget.usage().used == 1


def test_count_resets_on_a_new_quota_day(tmp_path):
    path = tmp_path / "usage.json"
    path.write_text(json.dumps({"day": "2000-01-01", "count": 500, "last_call": 0}))
    budget = LlmBudget(path, daily_limit=500, min_interval_seconds=0)
    assert budget.acquire() is True


def test_two_budgets_on_the_same_file_share_one_count(tmp_path):
    path = tmp_path / "usage.json"
    LlmBudget(path, daily_limit=1, min_interval_seconds=0).acquire()
    assert LlmBudget(path, daily_limit=1, min_interval_seconds=0).acquire() is False


def test_requests_are_spaced_by_the_minimum_interval(tmp_path, monkeypatch):
    path = tmp_path / "usage.json"
    sleeps: list[float] = []
    monkeypatch.setattr("app.services.llm_budget.time.sleep", sleeps.append)
    monkeypatch.setattr("app.services.llm_budget.time.time", lambda: 1000.0)
    path.write_text(json.dumps({"day": _quota_day(), "count": 0, "last_call": 998.0}))
    LlmBudget(path, daily_limit=5, min_interval_seconds=6.5).acquire()
    assert sleeps == [4.5]


def test_a_damaged_budget_file_counts_as_unused(tmp_path):
    from app.services.llm_budget import LlmBudget

    path = tmp_path / "budget.json"
    path.write_text("{not json")
    assert LlmBudget(path, daily_limit=5, min_interval_seconds=0).usage().used == 0
