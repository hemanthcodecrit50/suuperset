"""Playwright browser session management.

The portal is authenticated and JavaScript-driven. This module opens the normal
login page, reuses a stored authenticated browser state, and detects when the
session has expired (so a human can reauthenticate).

Security notes:
- Never bypass authentication, CAPTCHA, MFA or access controls.
- Never commit storage state, cookies or credentials to source control.
- Only read data the authenticated user is authorized to access.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class SessionExpiredError(RuntimeError):
    """Raised when the stored authenticated session is no longer valid."""


@dataclass
class BrowserSession:
    """Async context manager wrapping a Playwright browser page.

    Usage::

        async with BrowserSession() as session:
            page = await session.new_page()
            await page.goto(url)
    """

    settings: Settings
    headless: bool = True
    _playwright: Any = None
    _browser: Any = None
    _context: Any = None

    def __init__(self, settings: Settings | None = None, headless: bool = True) -> None:
        self.settings = settings or get_settings()
        self.headless = headless

    async def __aenter__(self) -> BrowserSession:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)

        storage_state = None
        state_path = self.settings.storage_state_path
        if state_path.is_file():
            storage_state = str(state_path)
            logger.info("Loaded authenticated browser state from %s", state_path)
        else:
            logger.warning(
                "No stored browser state at %s; run scripts/login.py first.",
                state_path,
            )

        self._context = await self._browser.new_context(
            storage_state=storage_state,
            locale="en-IN",
            timezone_id=self.settings.timezone,
        )
        self._context.set_default_navigation_timeout(self.settings.poll_nav_timeout_ms)
        self._context.set_default_timeout(self.settings.poll_nav_timeout_ms)

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def new_page(self) -> Any:
        if self._context is None:
            raise RuntimeError("BrowserSession not started; use it as a context manager.")
        return await self._context.new_page()

    async def goto(self, url: str, expect_app: bool = False) -> Any:
        """Navigate to ``url`` and return the page.

        Uses ``domcontentloaded`` and relies on the context-level timeout.
        Set ``expect_app=True`` to wait until the authenticated app shell has
        rendered (raises :class:`SessionExpiredError` on timeout).
        """
        page = await self.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        if expect_app:
            await wait_for_app(page)
        return page


async def is_logged_out(page: Any) -> bool:
    """Detect a logged-out / expired session.

    Verified against the real portal: the app serves an email/password login
    form at ``/students`` with a ``Login`` button, and app pages are titled
    ``Home`` (feed) or ``Job Profile`` (job list). A logged-out browser is
    either redirected away from ``/students`` or shows the password field.
    """
    from app.selectors import normalize, selectors_for

    selectors = selectors_for()
    url = (page.url or "").lower()

    # Redirected away from the app (SSO / login host).
    if "/students" not in url:
        return True

    # The password field is a reliable "not authenticated" signal.
    password_selector = normalize(selectors.login_password) or "input[type='password']"
    try:
        if await page.locator(password_selector).first.is_visible(timeout=1000):
            return True
    except Exception:  # pragma: no cover - selector may not exist
        pass

    # Titles that indicate an auth page.
    try:
        title = (await page.title()).strip().lower()
    except Exception:  # pragma: no cover - defensive
        title = ""
    if title and title not in {t.lower() for t in selectors.app_titles}:
        if any(word in title for word in ("login", "sign in", "signin", "sso")):
            return True

    return False


async def is_app_shell_ready(page: Any) -> bool:
    """True only once an authenticated app page has rendered, not while it is loading."""
    from app.selectors import selectors_for

    if await is_logged_out(page):
        return False
    try:
        title = (await page.title()).strip().lower()
    except Exception:  # pragma: no cover - page may be navigating
        return False
    return title in {name.lower() for name in selectors_for().app_titles}


async def assert_logged_in(page: Any) -> None:
    """Raise :class:`SessionExpiredError` if the page looks logged out."""
    if await is_logged_out(page):
        raise SessionExpiredError(
            "Superset session appears expired. Re-run scripts/login.py to reauthenticate."
        )


async def login_form_visible(page: Any) -> bool:
    """True when the portal's email/password login form is on screen."""
    from app.selectors import normalize, selectors_for

    selectors = selectors_for()
    password_selector = normalize(selectors.login_password) or "input[type='password']"
    try:
        return await page.locator(password_selector).first.is_visible(timeout=1000)
    except Exception:  # pragma: no cover - defensive
        return False


async def wait_for_app(page: Any, timeout_ms: int = 15_000) -> None:
    """Wait until the app shell renders (i.e. we are authenticated)."""
    deadline = timeout_ms
    waited = 0
    step = 1000
    while waited < deadline:
        if not await is_logged_out(page):
            return
        await page.wait_for_timeout(step)
        waited += step
    raise SessionExpiredError("Timed out waiting for the Superset app shell to load.")
