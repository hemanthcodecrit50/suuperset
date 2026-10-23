"""Interactive login bootstrap.

Opens the real Superset login page in a headed browser so the user can complete
institutional SSO/MFA manually. When the login succeeds, the authenticated
browser state is saved for the collector to reuse.

The saved state is a credential-equivalent secret: it must never be committed
and should be readable only by the user running the poller.
"""

from __future__ import annotations

import logging
import stat
import sys
from pathlib import Path

from app.collector.browser import is_logged_out
from app.config import get_settings


async def run_login(timeout_seconds: int = 300) -> int:
    from playwright.async_api import async_playwright

    settings = get_settings()
    state_path = settings.storage_state_path
    state_path.parent.mkdir(parents=True, exist_ok=True)

    login_url = settings.superset_login_url
    print(f"Opening {login_url}")
    print("Complete institutional SSO/MFA in the browser window.")
    print("The window closes automatically once login is detected.\n")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context(locale="en-IN", timezone_id=settings.timezone)
        page = await context.new_page()
        await page.goto(login_url, wait_until="domcontentloaded")

        # The portal renders an email/password form at /students; the user
        # completes it manually. We simply wait until the app shell loads.
        deadline_ms = timeout_seconds * 1000
        elapsed = 0
        step = 2000
        logged_in = False
        while elapsed < deadline_ms:
            if not await is_logged_out(page):
                logged_in = True
                break
            await page.wait_for_timeout(step)
            elapsed += step

        if not logged_in:
            print("Timed out waiting for login. No state was saved.", file=sys.stderr)
            await browser.close()
            return 1

        await context.storage_state(path=str(state_path))
        await browser.close()

    # Restrict permissions on the sensitive storage state.
    try:
        Path(state_path).chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

    print(f"Authenticated state saved to {state_path}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=get_settings().log_level)
    sys.exit(__import__("asyncio").run(run_login()))
