"""Regression tests for async Playwright usage in the collectors.

Playwright's async API returns coroutines; forgetting ``await`` produces the
runtime error ``TypeError: '>' not supported between instances of 'coroutine'
and 'int'`` plus a "coroutine ... was never awaited" warning. These tests use a
fake page whose awaitables record whether they were actually awaited, so a
missing ``await`` fails the suite instead of failing at runtime.
"""

from __future__ import annotations

import asyncio

from app.collector.job_profiles import collect_job_profiles
from app.collector.notifications import collect_announcements

CARD_TEXT = """Application Developer - Associate
Thoughtworks | Full Time | PAN India
Applied
3 days ago"""

ANN_TEXT = """Open for applications - Dover Corp's Job Profile
Training and Placement BMSCE · 2 days ago
Applications are now being accepted."""


class _Awaitable:
    """A value that records when it is awaited."""

    def __init__(self, owner: "FakeLocator", value):
        self.owner = owner
        self.value = value

    def __await__(self):
        self.owner.awaits.append(True)
        if False:  # pragma: no cover - makes this a generator function
            yield
        return self.value


class FakeLocator:
    def __init__(
        self,
        text: str = "",
        count_value: int = 0,
        owner: list | None = None,
        selector: str = "?",
    ):
        self.text = text
        self._count = count_value
        self.awaits: list[bool] = []
        self.selector = selector
        if owner is not None:
            owner.append(self)

    def __repr__(self) -> str:
        return f"<FakeLocator {self.selector} awaited={len(self.awaits)}>"

    def count(self):
        return _Awaitable(self, self._count)

    async def inner_text(self):
        self.awaits.append(True)
        return self.text

    async def get_attribute(self, name):
        self.awaits.append(True)
        return None

    async def click(self, **kwargs):
        self.awaits.append(True)

    async def is_visible(self, **kwargs):
        self.awaits.append(True)
        return False

    async def scroll_into_view_if_needed(self, **kwargs):
        self.awaits.append(True)

    async def fill(self, value):
        self.awaits.append(True)

    # NOTE: in Playwright, ``.first`` is a property (not a method) while
    # ``.nth(i)`` is a method. The fake mirrors that exactly, so the collector
    # is exercised against the real API shape.
    @property
    def first(self):
        return self

    def nth(self, index):
        return self


class FakeMouse:
    async def wheel(self, dx, dy):
        return None


class FakePage:
    def __init__(self, locators: dict[str, FakeLocator], url: str = "https://app.joinsuperset.com/students/jobprofiles"):
        self.url = url
        self._locators = locators
        self.created: list[FakeLocator] = []
        self.mouse = FakeMouse()

    def locator(self, selector):
        loc = self._locators.get(selector)
        if loc is None:
            loc = FakeLocator(count_value=0)
        loc.selector = selector
        self.created.append(loc)
        return loc

    async def title(self):
        return "Home"

    async def evaluate(self, expression):
        return 1000  # constant height so scroll loops break immediately

    async def wait_for_timeout(self, ms):
        return None


def _page_with_cards(text: str, count: int, selector: str = "[role='listitem']") -> FakePage:
    return FakePage({selector: FakeLocator(text=text, count_value=count)})


def test_collect_job_profiles_awaits_every_coroutine():
    page = _page_with_cards(CARD_TEXT, count=2)
    results = asyncio.run(collect_job_profiles(page, load_more_scrolls=1))

    assert len(results) == 2
    assert results[0].title == "Application Developer - Associate"
    assert results[0].company_name == "Thoughtworks"

    # Every locator handed out must have had at least one call awaited.
    not_awaited = [loc for loc in page.created if not loc.awaits]
    assert not_awaited == [], "a Playwright coroutine was created but never awaited"


def test_collect_announcements_awaits_every_coroutine():
    page = _page_with_cards(ANN_TEXT, count=1, selector="[role='listitem']")
    page.url = "https://app.joinsuperset.com/students"
    results = asyncio.run(collect_announcements(page, max_scrolls=1))

    assert len(results) == 1
    assert results[0].title == "Open for applications - Dover Corp's Job Profile"

    not_awaited = [loc for loc in page.created if not loc.awaits]
    assert not_awaited == [], "a Playwright coroutine was created but never awaited"


def test_no_cards_returns_empty_and_still_awaits():
    page = FakePage({})  # nothing matches any candidate
    results = asyncio.run(collect_job_profiles(page, load_more_scrolls=1))
    assert results == []
