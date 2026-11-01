"""Tests for telling a logged-in Superset page from one still loading."""

from __future__ import annotations

import asyncio

from app.collector.browser import is_app_shell_ready


class _Locator:
    def __init__(self, is_visible: bool) -> None:
        self.first = self
        self._is_visible = is_visible

    async def is_visible(self, timeout: int = 0) -> bool:
        return self._is_visible


class FakePage:
    def __init__(self, url: str, title: str, has_password_field: bool = False) -> None:
        self.url = url
        self._title = title
        self._has_password_field = has_password_field

    def locator(self, selector: str) -> _Locator:
        return _Locator(self._has_password_field)

    async def title(self) -> str:
        return self._title


def _ready(page: FakePage) -> bool:
    return asyncio.run(is_app_shell_ready(page))


def test_blank_loading_page_is_not_treated_as_logged_in():
    assert _ready(FakePage("https://app.joinsuperset.com/students", "")) is False


def test_login_form_is_not_logged_in():
    page = FakePage(
        "https://app.joinsuperset.com/students", "Superset", has_password_field=True
    )
    assert _ready(page) is False


def test_home_feed_counts_as_logged_in():
    assert _ready(FakePage("https://app.joinsuperset.com/students", "Home")) is True


def test_job_profile_page_counts_as_logged_in():
    assert (
        _ready(
            FakePage("https://app.joinsuperset.com/students/jobprofiles", "Job Profile")
        )
        is True
    )


def test_sso_redirect_is_not_logged_in():
    assert _ready(FakePage("https://login.example.edu/sso", "Home")) is False
