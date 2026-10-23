"""All Superset portal selectors in one configurable place.

Verified from a working Selenium script (sample_images/main.py) plus the
screenshots. Values that are ``None`` fall back to text-based heuristics, so
the collectors work before and after more identifiers are supplied.

Accepted formats per value:
- ``#mui-component-select-jobApplicationStatus``   CSS id (verified)
- ``#demo_step_3``                                 CSS id (verified)
- ``div.job-card > h3 a``                          CSS selector (preferred)
- ``//div[@class='job-card']`` / ``xpath=...``     XPath (also supported)
- ``data-testid=job-row``                          test-id shorthand (also supported)

MUI notes: the portal is Material UI. MUI reuses ``id="mui-component-select-<name>"``
on selects and renders menu options as ``li`` under ``ul[role=listbox]``, so
those are reliable anchors once the component names are known.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, fields, replace

logger = logging.getLogger(__name__)


def normalize(selector: str | None) -> str | None:
    """Convert test-id shorthand / xpath= prefixes into a Playwright selector."""
    if not selector:
        return None
    selector = selector.strip()
    if not selector:
        return None
    if selector.lower().startswith("data-testid="):
        return f"[data-testid='{selector.split('=', 1)[1]}']"
    if selector.lower().startswith("data-cy="):
        return f"[data-cy='{selector.split('=', 1)[1]}']"
    if selector.startswith("xpath="):
        return selector  # playwright accepts xpath=... directly
    return selector


@dataclass(frozen=True)
class Selectors:
    # --- Login / session (verified via sample_images/main.py) -----------
    # The portal renders an email/password form at /students with a "Login"
    # button; the original ids («r5»/«r6») are React-generated and unstable,
    # so type-based selectors are used instead.
    login_email: str | None = "input[type='email'], input[name='email']"
    login_password: str | None = "input[type='password']"
    login_submit: str | None = "xpath=//button[normalize-space()='Login']"
    # Page titles observed: "Home" (feed), "Job Profile" (job list).
    app_titles: tuple[str, ...] = ("Home", "Job Profile")

    # --- Navigation (verified) ------------------------------------------
    # Sidebar buttons use demo_step_N ids; #demo_step_3 is Job Profiles.
    nav_job_profiles: str | None = "#demo_step_3"

    # --- Home feed (announcements, /students) --------------------------
    announcement_card: str | None = None          # each feed item / card
    announcement_title: str | None = None         # title element inside card
    announcement_poster_time: str | None = None   # "poster · 2 days ago" line
    announcement_body: str | None = None          # body container
    announcement_link: str | None = None          # anchor inside card
    announcement_applicable_courses: str | None = None
    announcement_eligibility: str | None = None
    announcement_new_badge: str | None = None
    feed_scroll_container: str | None = None      # lazy-load container

    # --- Job list (/students/jobprofiles) ------------------------------
    job_card: str | None = None                   # each job card
    job_title: str | None = None                  # title element (clickable)
    job_company_meta: str | None = None           # "Company | WorkType | Location"
    job_status_badge: str | None = None           # status badge
    job_closes_in: str | None = None              # "Closes in N days"
    job_search_box: str | None = None             # search input placeholder
    # MUI Select for application status (verified id).
    job_status_filter: str | None = "#mui-component-select-jobApplicationStatus"
    # Menu options render as li inside a MUI listbox.
    job_status_options: str | None = "ul[role='listbox'] li, .MuiMenu-list li"
    job_tab_all: str | None = None
    job_tab_applied: str | None = None
    job_list_scroll_container: str | None = None

    # --- Status filter values (verified) --------------------------------
    status_option_values: tuple[str, ...] = (
        "All",
        "Applied",
        "Yet To Apply",
        "Not Interested",
        "Withdrawn",
        "Rejected",
        "Offered",
        "Offer Accepted",
        "Offer Rejected",
    )

    # --- Detail panel (SPA, right side) ---------------------------------
    detail_panel: str | None = None               # whole panel container
    detail_opening_overview: str | None = None
    detail_category: str | None = None
    detail_role: str | None = None
    detail_job_functions: str | None = None
    detail_compensation: str | None = None
    detail_other_info: str | None = None
    detail_job_description: str | None = None
    detail_hiring_workflow: str | None = None
    detail_eligibility: str | None = None
    detail_additional_questions: str | None = None
    detail_required_skills: str | None = None


def default_selectors() -> Selectors:
    """Verified defaults (see module docstring)."""
    return Selectors()


def selectors_for(source: str = "superset") -> Selectors:
    """Return selectors, optionally overridden by a JSON file.

    Set ``SUPERSET_SELECTORS_FILE=/path/selectors.json`` to override any field
    without editing code. Unknown keys are ignored; invalid JSON is logged and
    ignored so collection still proceeds with the defaults.
    """
    selectors = default_selectors()
    path = os.getenv("SUPERSET_SELECTORS_FILE", "").strip()
    if not path:
        return selectors
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("selectors file must contain a JSON object")
    except Exception as exc:  # noqa: BLE001 - config errors must not break collection
        logger.warning("Could not read selectors file %s: %s", path, exc)
        return selectors

    valid = {f.name for f in fields(Selectors)}
    overrides = {k: v for k, v in data.items() if k in valid}
    unknown = set(data) - valid
    if unknown:
        logger.warning("Ignoring unknown selector keys: %s", sorted(unknown))
    if overrides:
        logger.info("Loaded %d selector overrides from %s", len(overrides), path)
    return replace(selectors, **overrides)