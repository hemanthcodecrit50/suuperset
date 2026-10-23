"""Parse the Superset home feed (placement announcements).

Structure verified from screenshots (sample_images/home_page.png, Oct 2026).
The feed lives at ``/students``. Each announcement card contains:

    <title line>
    <poster> · <relative time>        e.g. "Training and Placement BMSCE · 2 days ago"
    <body paragraphs>
    Applicable Courses ... Eligibility ... See More          (per-card sections)

Exact DOM classes are unknown, so rows are found by probing candidate selectors
and all values are extracted from rendered text. Final selector confirmation
should happen against the live portal (see README "Verifying the portal").
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.collector.browser import assert_logged_in

logger = logging.getLogger(__name__)

# TODO(verify): order = preference. The first selector that matches >=1 element
# wins. Update these after inspecting the live DOM once, or fill in
# ``app/selectors.py`` which takes precedence.
CARD_SELECTOR_CANDIDATES = (
    "[role='listitem']",
    "[class*='card']",
    "[class*='feed'] [class*='item']",
    "article",
)

TIME_AGO_PATTERN = re.compile(r"(?:· )?(\d+)\s*(second|minute|hour|day)s?\s*ago", re.IGNORECASE)

# Relative-time strings the portal uses on announcement cards.
RELATIVE_TIME_RE = re.compile(
    r"(?:·)?\s*(?:just now|\d+\s*(?:seconds?|minutes?|hours?|days?)\s*ago)", re.IGNORECASE
)

# Lines that appear as section headings inside an announcement card.
CARD_SECTION_HEADINGS = ("Applicable Courses", "Eligibility", "See More", "New")


@dataclass
class RawAnnouncement:
    """Normalized announcement as read from the portal."""

    external_id: str
    title: str
    body: str = ""
    url: str | None = None
    category: str | None = None
    published_at: str | None = None
    posted_by: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


async def _find_cards(page: Any, selector: str | None = None) -> Any:
    """Return the best-matching row locator via candidate probe."""
    from app.selectors import normalize

    preferred = normalize(selector)
    best = None
    best_count = 0
    candidates = ([preferred] if preferred else []) + list(CARD_SELECTOR_CANDIDATES)
    for candidate in candidates:
        locator = page.locator(candidate)
        try:
            count = await locator.count()
        except Exception:  # pragma: no cover - invalid selector in older browsers
            continue
        if count > best_count:
            best, best_count = locator, count
    if best_count == 0:
        logger.warning(
            "No announcement cards matched any of %s. "
            "Verify selectors against the live portal.",
            candidates,
        )
    else:
        logger.info("Announcement cards: matched %s elements", best_count)
    return best


async def _card_text(row: Any) -> str:
    try:
        return (await row.inner_text()).strip()
    except Exception:  # pragma: no cover - defensive
        return ""


def _parse_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


async def _assign_external_id(row: Any, text: str) -> str:
    for selector, attr in (("[data-id]", "data-id"), ("a[href]", "href")):
        try:
            locator = row.locator(selector).first
            if await locator.count() > 0:
                value = await locator.get_attribute(attr)
                if value:
                    return value.rsplit("/", 1)[-1].split("?")[0]
        except Exception:  # pragma: no cover - defensive
            continue
    # Stable fallback: hash of the visible text so re-polls keep the same id.
    return "ann:" + format(abs(hash(text)), "x")


async def _parse_row(row: Any) -> RawAnnouncement:
    text = await _card_text(row)
    lines = _parse_lines(text)

    title = next((line for line in lines if not RELATIVE_TIME_RE.search(line)), lines[0] if lines else "")
    posted_by = next(
        (line for line in lines if RELATIVE_TIME_RE.search(line)), None
    )
    body_lines = [
        line
        for line in lines[1:]
        if line != title
        and not RELATIVE_TIME_RE.search(line)
        and not any(line.startswith(h) or line == h for h in CARD_SECTION_HEADINGS)
    ]
    body = "\n".join(body_lines)

    category = None
    for line in lines:
        low = line.lower()
        if low.startswith("applicable courses"):
            category = "applicable_courses"
        elif low.startswith("eligibility"):
            category = "eligibility"
        elif low.startswith("applications"):
            category = "application_update"

    external_id = await _assign_external_id(row, text)

    return RawAnnouncement(
        external_id=external_id,
        title=title or "(untitled announcement)",
        body=body,
        posted_by=posted_by,
        published_at=None,  # portal shows relative time only; no absolute date
        category=category,
        raw={"lines": lines, "card_text": text[:4000]},
    )


async def _scroll_feed(page: Any, max_scrolls: int = 10) -> None:
    """Scroll the page to trigger lazy-loading of older announcements."""
    for _ in range(max_scrolls):
        before = await page.evaluate("document.body.scrollHeight")
        await page.mouse.wheel(0, 4000)
        await page.wait_for_timeout(600)
        after = await page.evaluate("document.body.scrollHeight")
        if after <= before:
            break


async def collect_announcements(
    page: Any, max_scrolls: int = 10, card_selector: str | None = None
) -> list[RawAnnouncement]:
    """Collect announcements from the home feed (``/students``).

    ``card_selector`` is an optional exact selector for each feed card; when
    omitted, candidate selectors are probed.
    """
    from app.selectors import selectors_for

    configured = selectors_for()
    card_selector = card_selector or configured.announcement_card
    await assert_logged_in(page)
    await _scroll_feed(page, max_scrolls=max_scrolls)

    cards = await _find_cards(page, card_selector)
    results: list[RawAnnouncement] = []
    if cards is None:
        return results

    count = await cards.count()
    for index in range(count):
        results.append(await _parse_row(cards.nth(index)))

    logger.info("Collected %d announcements", len(results))
    return results