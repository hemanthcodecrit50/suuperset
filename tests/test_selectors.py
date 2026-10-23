"""Tests for the centralized selector registry."""

from __future__ import annotations

import json

import pytest

from app.selectors import Selectors, default_selectors, normalize, selectors_for


def test_normalize_shorthand():
    assert normalize("data-testid=job-row") == "[data-testid='job-row']"
    assert normalize("data-cy=card") == "[data-cy='card']"
    assert normalize("xpath=//div") == "xpath=//div"
    assert normalize("#demo_step_3") == "#demo_step_3"
    assert normalize(None) is None
    assert normalize("  ") is None


def test_verified_defaults():
    s = default_selectors()
    assert s.nav_job_profiles == "#demo_step_3"
    assert s.job_status_filter == "#mui-component-select-jobApplicationStatus"
    assert "Yet To Apply" in s.status_option_values
    assert "Offer Rejected" in s.status_option_values
    assert "Home" in s.app_titles


def test_selectors_json_overrides(tmp_path, monkeypatch):
    path = tmp_path / "selectors.json"
    path.write_text(json.dumps({"announcement_card": "#feed .card", "bogus_key": 1}))
    monkeypatch.setenv("SUPERSET_SELECTORS_FILE", str(path))
    s = selectors_for()
    assert s.announcement_card == "#feed .card"
    # Untouched keys keep defaults; unknown keys are ignored.
    assert s.nav_job_profiles == "#demo_step_3"


def test_selectors_invalid_json_is_ignored(tmp_path, monkeypatch):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    monkeypatch.setenv("SUPERSET_SELECTORS_FILE", str(path))
    assert selectors_for() == default_selectors()


def test_selectors_missing_file_is_ignored(monkeypatch):
    monkeypatch.setenv("SUPERSET_SELECTORS_FILE", "/nonexistent/selectors.json")
    assert selectors_for() == default_selectors()