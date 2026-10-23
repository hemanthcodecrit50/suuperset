"""Headless smoke tests for every dashboard page.

Streamlit's AppTest executes the real script and surfaces runtime exceptions,
so this catches errors that only appear when a page actually renders.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

DASHBOARD = Path(__file__).resolve().parent.parent / "app" / "dashboard.py"

PAGES = ["Overview", "Postings", "Documents", "Alerts"]


def _run(page: str) -> AppTest:
    app = AppTest.from_file(str(DASHBOARD), default_timeout=90)
    app.run()
    assert not app.exception, f"initial render failed: {[e.value for e in app.exception]}"
    app.sidebar.radio[0].set_value(page).run()
    return app


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_exception(page):
    app = _run(page)
    assert not app.exception, (
        f"{page} raised: {[getattr(e, 'value', e) for e in app.exception]}"
    )


def test_postings_page_exposes_filters():
    app = _run("Postings")
    assert app.sidebar.text_input, "expected a search filter"


def test_overview_reports_health():
    app = _run("Overview")
    labels = {metric.label for metric in app.metric}
    assert "Last run" in labels
    assert "Postings tracked" in labels