"""Parse the Superset job-profiles listing and detail panel.

Structure verified from screenshots (sample_images/*.png, Oct 2026):

Listing at ``/students/jobprofiles``:
- Tabs: "All Jobs" | "Applied Jobs"
- Filters: Job Sector | Position Type | Status | Sort by, plus a search box
  with placeholder "Search by job title or company" and "Clear all filters".
- Sort options: Created At, Job Profile Title, Company Name, Location, Status,
  Application Deadline.
- Card: title | "Company | WorkType | Location" · "Company · Location" ·
  relative time · status badge · "Closes in N days".

Clicking a card opens the detail panel (SPA, no navigation):
- Opening Overview: Category, Role, Job Functions, Job Profile CTC, Other Info
- Job Description, Hiring Workflow, Eligibility Criteria, Additional Questions
- Required skills

Exact DOM classes are unknown, so rows are found by probing candidate selectors
and all values are extracted from rendered text.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.collector.browser import assert_logged_in

logger = logging.getLogger(__name__)

# TODO(verify): order = preference; first selector with matches wins.
CARD_SELECTOR_CANDIDATES = (
    "[role='listitem']",
    "[class*='card']",
    "[class*='job']",
)

RELATIVE_TIME_RE = re.compile(
    r"\d+\s*(?:seconds?|minutes?|hours?|days?)\s*ago", re.IGNORECASE
)
CLOSES_IN_RE = re.compile(r"closes\s+in\s+(\d+)\s*days?", re.IGNORECASE)


STATUS_WORDS = (
    "applied",
    "applications closed",
    "yet to apply",
    "not interested",
    "withdrawn",
    "rejected",
    "offered",
    "offer accepted",
    "offer rejected",
    "shortlisted",
    "assessment",
)

# Detail-panel metadata labels (text prefix inside "Opening Overview").
META_LABELS = {
    "category": "Category",
    "role": "Role",
    "job_function": "Job Functions",
    "compensation": "Job Profile CTC",
    "other_info": "Other Info",
    "location": "Location",
    "company_name": "Company",
}

# Section headings in the detail panel, in document order.
DETAIL_SECTIONS = (
    "Opening Overview",
    "Job Description",
    "Hiring Workflow",
    "Eligibility Criteria",
    "Additional Questions",
    "Required skills",
)


@dataclass
class RawOpportunity:
    """Normalized job opening as read from the portal."""

    external_id: str
    title: str
    company_name: str | None = None
    location: str | None = None
    work_type: str | None = None
    compensation: str | None = None
    eligibility: str | None = None
    job_function: str | None = None
    skills: list[str] = field(default_factory=list)
    status: str | None = None
    closes_in_days: int | None = None
    deadline: datetime | None = None
    apply_url: str | None = None
    description: str = ""
    attachments: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


async def _find_cards(page: Any, selector: str | None = None) -> Any | None:
    from app.selectors import normalize

    preferred = normalize(selector)
    best = None
    best_count = 0
    candidates = ([preferred] if preferred else []) + list(CARD_SELECTOR_CANDIDATES)
    for candidate in candidates:
        locator = page.locator(candidate)
        try:
            count = await locator.count()
        except Exception:  # pragma: no cover - defensive
            continue
        if count > best_count:
            best, best_count = locator, count
    if best_count == 0:
        logger.warning(
            "No job-profile cards matched any of %s. Verify against the live portal.",
            candidates,
        )
        return None
    logger.info("Job-profile cards: matched %s elements", best_count)
    return best


async def _card_text(row: Any) -> str:
    try:
        return (await row.inner_text()).strip()
    except Exception:  # pragma: no cover - defensive
        return ""


def _parse_status(text: str) -> str | None:
    low = text.lower()
    for word in STATUS_WORDS:
        if word in low:
            return word.title()
    return None


def _parse_meta_line(text: str) -> tuple[str | None, str | None, str | None]:
    """Parse the "Company | WorkType | Location" line."""
    parts = [p.strip() for p in re.split(r"\s*[|·]\s*", text.strip())]
    return (parts[0], parts[1] if len(parts) > 1 else None, parts[2] if len(parts) > 2 else None)


async def _parse_listing_row(row: Any) -> tuple[RawOpportunity, str | None]:
    text = await _card_text(row)
    raw = parse_listing_text(text)
    href = None
    try:
        link = row.locator("a[href]").first
        if await link.count() > 0:
            href = await link.get_attribute("href")
    except Exception:  # pragma: no cover - defensive
        pass
    raw.apply_url = href
    return raw, href


def parse_listing_text(text: str) -> RawOpportunity:
    """Parse one card's rendered text into a RawOpportunity (sync, testable)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    title = next(
        (line for line in lines if not RELATIVE_TIME_RE.search(line) and len(line) > 3),
        "",
    )

    company = location = work_type = None
    status = _parse_status(text)
    closes_in = None

    for line in lines:
        closes = CLOSES_IN_RE.search(line)
        if closes:
            closes_in = int(closes.group(1))
        if RELATIVE_TIME_RE.search(line):
            continue
        if "|" in line:
            company, work_type, location = _parse_meta_line(line)
        elif "·" in line:
            left, _, right = line.partition("·")
            company = company or left.strip() or None
            location = location or right.strip() or None

    return RawOpportunity(
        external_id="",
        title=title or "(untitled role)",
        company_name=company,
        location=location,
        work_type=work_type,
        status=status,
        closes_in_days=closes_in,
        raw={"card_text": text[:4000], "lines": lines},
    )


def parse_detail_panel(panel_text: str) -> dict[str, Any]:
    """Parse the right-side detail panel from its rendered text.

    Metadata lines look like ``Category: Tier 2``. Longer sections are sliced
    between known headings. Returns a dict with keys for metadata and each
    section.
    """
    lines = [line.strip() for line in panel_text.splitlines() if line.strip()]
    result: dict[str, Any] = {"sections": {}}
    meta_pairs: dict[str, list[str]] = {}

    # metadata: "Label: value"
    for line in lines:
        for key, label in META_LABELS.items():
            if line.startswith(label + ":"):
                meta_pairs.setdefault(key, []).append(line[len(label) + 1 :].strip())

    for key, values in meta_pairs.items():
        result[key] = ", ".join(v for v in values if v) or None

    # Required skills may appear as "Required skills: ..." or a heading.
    skills: list[str] = []
    for line in lines:
        if line.lower().startswith("required skills"):
            rest = line.split(":", 1)[1].strip() if ":" in line else ""
            if rest and "no skills" not in rest.lower():
                skills = [s.strip() for s in rest.split(",") if s.strip()]

    sections = {}
    current: str | None = None
    for line in lines:
        matched = next(
            (s for s in DETAIL_SECTIONS if line.lower().startswith(s.lower())), None
        )
        if matched:
            current = matched
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)
    result["skills"] = skills
    for name, content in sections.items():
        result["sections"][name] = "\n".join(content).strip()

    # Skills may also live under the "Required skills" heading.
    req = result["sections"].get("Required skills")
    if req and "no skills" not in req.lower():
        parts = [p.strip() for p in req.replace("\n", ",").split(",")]
        result["skills"] = [p for p in parts if p]

    return result


def _current_job_id(page: Any) -> str | None:
    try:
        parsed = urlparse(page.url)
    except Exception:  # pragma: no cover - defensive
        return None
    query = {k.lower(): v for k, v in parse_qs(parsed.query).items()}
    for key in ("currentjobid", "currentjobld", "jobid"):
        values = query.get(key)
        if values:
            return values[0]
    return None


async def _click_and_read_detail(
    page: Any,
    row: Any,
    raw: RawOpportunity,
    *,
    title_selector: str | None = None,
    detail_panel_selector: str | None = None,
) -> RawOpportunity:
    """Click a card title and merge the detail panel into ``raw``."""
    try:
        await row.scroll_into_view_if_needed()
        if title_selector:
            await row.locator(title_selector).first.click(timeout=5000)
        else:
            await row.click()
        await page.wait_for_timeout(800)  # SPA panel render
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not click job card %r: %s", raw.title, exc)
        return raw

    external_id = _current_job_id(page)
    if external_id:
        raw.external_id = external_id
    raw.raw["detail_url"] = page.url

    try:
        panel = page.locator(detail_panel_selector or DETAIL_PANEL_CANDIDATES[0])
        text = await panel.inner_text()
    except Exception:  # pragma: no cover - defensive
        text = await _page_body_text_after_click(page)

    detail = parse_detail_panel(text or "")
    if detail.get("sections"):
        raw.raw["detail_panel"] = detail
    for key in ("company_name", "location", "job_function", "compensation", "eligibility"):
        value = detail.get(key)
        if value and not getattr(raw, key):
            setattr(raw, key, value)
    if detail.get("skills"):
        raw.skills = detail["skills"]
    raw.description = detail.get("sections", {}).get("Job Description") or raw.description
    raw.eligibility = (
        detail.get("sections", {}).get("Eligibility Criteria") or raw.eligibility
    )
    return raw


DETAIL_PANEL_CANDIDATES = ("[class*='detail']", "[class*='overview']", "main", "body")


async def _page_body_text_after_click(page: Any) -> str:
    try:
        return await page.locator("body").inner_text()
    except Exception:  # pragma: no cover - defensive
        return ""


async def _scroll_list(page: Any, max_scrolls: int = 15) -> None:
    for _ in range(max_scrolls):
        before = await page.evaluate("document.body.scrollHeight")
        await page.mouse.wheel(0, 4000)
        await page.wait_for_timeout(500)
        after = await page.evaluate("document.body.scrollHeight")
        if after <= before:
            break


async def open_job_profiles(page: Any) -> None:
    """Navigate to Job Profiles via the sidebar button.

    Verified id: ``#demo_step_3`` (sample_images/main.py). Navigating directly
    to ``/students/jobprofiles`` also works, so failures here are non-fatal.
    """
    from app.selectors import normalize, selectors_for

    selector = normalize(selectors_for().nav_job_profiles)
    if not selector:
        return
    try:
        await page.locator(selector).first.click(timeout=5000)
        await page.wait_for_timeout(1000)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not click Job Profiles nav (%s): %s", selector, exc)


async def apply_status_filter(page: Any, status: str) -> bool:
    """Filter the job list by application status.

    Verified flow (MUI): click ``#mui-component-select-jobApplicationStatus``,
    then click the matching option in the opened menu. Returns True on success.
    """
    from app.selectors import normalize, selectors_for

    selectors = selectors_for()
    trigger = normalize(selectors.job_status_filter)
    if not trigger:
        logger.warning("No status-filter selector configured.")
        return False

    known = selectors.status_option_values
    if status not in known:
        logger.warning("Unknown status %r (known: %s)", status, ", ".join(known))
        return False

    try:
        await page.locator(trigger).first.click(timeout=5000)
        await page.wait_for_timeout(400)
        options = page.locator(normalize(selectors.job_status_options) or "li")
        count = await options.count()
        for index in range(count):
            option = options.nth(index)
            text = (await option.inner_text()).strip()
            if text == status:
                await option.click()
                await page.wait_for_timeout(800)
                logger.info("Applied status filter: %s", status)
                return True
        logger.warning("Status option %r not found among %d options", status, count)
        await page.keyboard.press("Escape")
        return False
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not apply status filter %r: %s", status, exc)
        return False


async def switch_tab(page: Any, tab: str = "all") -> bool:
    """Switch between the 'All Jobs' and 'Applied Jobs' tabs."""
    from app.selectors import normalize, selectors_for

    selectors = selectors_for()
    label = "All Jobs" if tab == "all" else "Applied Jobs"
    selector = normalize(
        selectors.job_tab_all if tab == "all" else selectors.job_tab_applied
    )
    try:
        target = page.locator(selector).first if selector else page.get_by_role(
            "tab", name=label
        ).first
        await target.click(timeout=5000)
        await page.wait_for_timeout(800)
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not switch to %r tab: %s", label, exc)
        return False


async def collect_job_profiles(
    page: Any,
    *,
    search: str | None = None,
    status: str | None = None,
    tab: str | None = None,
    load_more_scrolls: int = 15,
) -> list[RawOpportunity]:
    """Collect job profiles from ``/students/jobprofiles``.

    Optionally filter by tab (``"all"``/``"applied"``), application ``status``
    (e.g. ``"Yet To Apply"``) or ``search`` text before reading the list — this
    keeps fetch size small during verification. Exact selectors from
    ``app/selectors.py`` take precedence over probe-based locators.
    """
    from app.selectors import normalize, selectors_for

    configured = selectors_for()
    card_selector = normalize(configured.job_card)
    title_selector = normalize(configured.job_title)
    detail_panel_selector = normalize(configured.detail_panel)
    search_placeholder = configured.job_search_box

    await assert_logged_in(page)

    if tab:
        await switch_tab(page, tab)

    if status:
        await apply_status_filter(page, status)

    if search:
        try:
            if search_placeholder:
                searchbox = page.locator(search_placeholder).first
            else:
                searchbox = page.get_by_placeholder("Search by job title or company")
            await searchbox.fill(search)
            await page.wait_for_timeout(800)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Could not fill job search box: %s", exc)

    await _scroll_list(page, max_scrolls=load_more_scrolls)

    cards = await _find_cards(page, card_selector)
    results: list[RawOpportunity] = []
    if cards is None:
        return results

    count = await cards.count()
    for index in range(count):
        row = cards.nth(index)
        raw, _href = await _parse_listing_row(row)
        raw = await _click_and_read_detail(
            page,
            row,
            raw,
            title_selector=title_selector,
            detail_panel_selector=detail_panel_selector,
        )
        if not raw.external_id:
            raw.external_id = "job:" + format(abs(hash(raw.raw.get("card_text", ""))), "x")
        results.append(raw)

    logger.info("Collected %d job profiles", len(results))
    return results