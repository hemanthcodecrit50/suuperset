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

from app.collector.browser import is_app_shell_ready
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
        context = await browser.new_context(
            locale="en-IN", timezone_id=settings.timezone
        )
        page = await context.new_page()
        await page.goto(login_url, wait_until="domcontentloaded")

        # A blank, still-loading page shows no login form either, so wait for
        # the real app shell and require it on two checks in a row.
        deadline_ms = timeout_seconds * 1000
        elapsed = 0
        step = 2000
        ready_checks = 0
        logged_in = False
        while elapsed < deadline_ms:
            await page.wait_for_timeout(step)
            elapsed += step
            ready_checks = ready_checks + 1 if await is_app_shell_ready(page) else 0
            if ready_checks >= 2:
                logged_in = True
                break

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
