"""Map Superset API JSON onto the collector dataclasses.

Keeps the rest of the system (change detection, DB, alerts) unaware of where
the data came from — API or DOM fallback.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.collector.api import SupersetApi, epoch_ms_to_datetime, html_to_text
from app.collector.job_profiles import RawOpportunity
from app.collector.notifications import RawAnnouncement

logger = logging.getLogger(__name__)


def _iso(value: Any) -> str | None:
    """Epoch-ms timestamp -> ISO string (safe for JSON columns)."""
    parsed = epoch_ms_to_datetime(value)
    return parsed.isoformat() if parsed else None


def _compensation(detail: dict) -> str | None:
    """Build a human-readable CTC string from the detail payload."""
    profile = detail.get("jobProfile") or {}
    parts: list[str] = []

    for value in (detail.get("offeredCtc"), profile.get("effectiveCTC")):
        if isinstance(value, (int, float)) and value:
            interval = profile.get("ctcInterval") or detail.get("ctcInterval") or ""
            currency = profile.get("ctcCurrency") or detail.get("offeredCtcCurrency") or ""
            label = "Offered" if value == detail.get("offeredCtc") else "Effective"
            entry = f"{label} {int(value):,} {currency}".strip()
            if interval:
                entry += f" ({interval.lower()})"
            parts.append(entry)
            break

    ctc_min, ctc_max = profile.get("ctcMin"), profile.get("ctcMax")
    low = int(ctc_min) if isinstance(ctc_min, (int, float)) else None
    high = int(ctc_max) if isinstance(ctc_max, (int, float)) and ctc_max else None
    if low or high:
        currency = profile.get("ctcCurrency") or ""
        interval = profile.get("ctcInterval") or ""
        if low and high:
            rng = f"{low:,}-{high:,}"
        elif high:
            rng = f"up to {high:,}"
        else:
            rng = f"from {low:,}"
        entry = f"CTC {rng} {currency}".strip()
        if interval:
            entry += f" ({interval.lower()})"
        parts.append(entry)

    return "; ".join(parts) if parts else None


def _eligibility(detail: dict) -> str | None:
    """Summarise eligibility for this student from the detail payload."""
    result = detail.get("eligibilityCheckResult") or {}
    if isinstance(result, dict) and result.get("result") is not None:
        verdict = result.get("result")
        if isinstance(verdict, bool):
            return "Eligible" if verdict else "Not eligible"
        if verdict:
            return str(verdict)

    profile = detail.get("jobProfile") or {}
    chunks: list[str] = []
    for key in ("eligibilities", "genericEligibilities", "workExEligibilities"):
        items = profile.get(key) or []
        for item in items:
            if isinstance(item, dict):
                label = item.get("name") or item.get("title") or item.get("description")
                if label:
                    chunks.append(html_to_text(str(label))[:120])
            elif isinstance(item, str):
                chunks.append(html_to_text(item)[:120])
    return "; ".join(chunks) if chunks else None


def _courses(detail: dict) -> list[str]:
    profile = detail.get("jobProfile") or {}
    names: list[str] = []
    for course in profile.get("applicableCourses") or []:
        if isinstance(course, dict):
            label = course.get("courseName") or course.get("name")
            if label:
                names.append(str(label))
        elif isinstance(course, str):
            names.append(course)
    return names


def map_job_profile(item: dict, detail: dict | None = None) -> RawOpportunity:
    """Map one ``/job_profiles`` entry (+ optional detail) to RawOpportunity."""
    detail = detail or {}
    profile = detail.get("jobProfile") or {}

    description = html_to_text(profile.get("jobDescription") or detail.get("jobDescription"))
    category = item.get("placementCategoryName") or (
        (profile.get("placementCategory") or {}).get("name")
        if isinstance(profile.get("placementCategory"), dict)
        else None
    )
    sector = item.get("sectorName")
    if not sector:
        sector_map = profile.get("sectorMap") or {}
        if isinstance(sector_map, dict) and sector_map:
            sector = ", ".join(str(v) for v in sector_map.values() if v)

    status = item.get("jobApplicationStatus") or item.get("jobProfileStatus")

    return RawOpportunity(
        external_id=item.get("jobProfileIdentifier") or "",
        title=item.get("jobProfileTitle") or "(untitled role)",
        company_name=item.get("companyName"),
        location=item.get("jobProfileLocation") or profile.get("location"),
        work_type=item.get("positionTypeLabel") or item.get("positionType"),
        compensation=_compensation(detail) if detail else None,
        eligibility=_eligibility(detail) if detail else None,
        job_function=sector,
        skills=[],
        status=status,
        closes_in_days=None,
        deadline=epoch_ms_to_datetime(item.get("jobProfileApplicationDeadline")),
        apply_url=None,
        description=description,
        attachments=[],
        raw={
            "job_profile_status": item.get("jobProfileStatus"),
            "application_status": item.get("jobApplicationStatus"),
            "placement_student_status": item.get("placementStudentStatus"),
            "placement_name": item.get("placementName"),
            "category": category,
            # ISO strings: this dict is persisted to a JSON column.
            "created_at": _iso(item.get("createdAt")),
            "deadline_epoch_ms": item.get("jobProfileApplicationDeadline"),
            "last_applied_at": _iso(item.get("jobApplicationLastAppliedAt")),
            "documents": item.get("documents") or [],
            "applicable_courses": _courses(detail) if detail else [],
            "no_of_vacancies": (profile.get("noOfVacancies") if detail else None),
            "resume_mandatory": (profile.get("resumeMandatory") if detail else None),
            "source": "api",
        },
    )


_NOTICE_PATH = "/students/notices"
_JOB_PATH = "/students/jobprofiles"


def map_notification(item: dict, base_url: str) -> RawAnnouncement:
    """Map one user-notification entry to RawAnnouncement."""
    params = item.get("urlParamMap") or {}
    body = html_to_text(item.get("content"))

    object_type = (item.get("object") or "").upper()
    url = None
    if "JOBPROFILE" in object_type and params.get("job_profile_id"):
        url = f"{base_url.rstrip('/')}{_JOB_PATH}?currentJobId={params['job_profile_id']}"
    elif params.get("notice_uuid"):
        url = f"{base_url.rstrip('/')}{_NOTICE_PATH}/{params['notice_uuid']}"

    lines = [line for line in body.splitlines() if line]
    title = lines[0] if lines else "(untitled notification)"
    # Most notifications are a single sentence; keep the full text as the body
    # rather than dropping it when there is no second line.
    full_body = body if len(lines) <= 1 else "\n".join(lines[1:])

    return RawAnnouncement(
        external_id=item.get("uuid") or "",
        title=title[:300],
        body=full_body,
        url=url,
        category=(object_type or item.get("action") or None),
        published_at=epoch_ms_to_datetime(item.get("createdAt")),
        posted_by=item.get("action"),
        raw={
            "object": item.get("object"),
            "action": item.get("action"),
            "status": item.get("status"),
            "url_params": params,
            "content_html": item.get("content"),
            "source": "api",
        },
    )


async def collect_announcements_api(
    api: SupersetApi, max_pages: int = 20
) -> list[RawAnnouncement]:
    """Collect announcements from the notifications API."""
    items = await api.notifications(max_pages=max_pages)
    results = [map_notification(item, api.base_url) for item in items]
    logger.info("Collected %d announcements via API", len(results))
    return results


async def collect_job_profiles_api(
    api: SupersetApi,
    *,
    include_detail: bool = True,
    detail_limit: int | None = None,
    status: str | None = None,
    need_detail=None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[RawOpportunity]:
    """Collect job profiles from the API, optionally fetching full details.

    Each detail call is one extra HTTP request, so ``need_detail`` lets the
    caller restrict fetches to genuinely new/changed roles. ``detail_limit``
    caps the number of requests per run.
    """
    items = await api.job_profiles()
    if status:
        wanted = status.strip().lower()
        items = [
            i
            for i in items
            if wanted in str(i.get("jobApplicationStatus") or "").lower()
            or wanted in str(i.get("jobProfileStatus") or "").lower()
        ]

    results: list[RawOpportunity] = []
    fetched = 0
    for index, item in enumerate(items, start=1):
        if on_progress is not None:
            on_progress(index, len(items))
        detail = None
        if include_detail:
            wanted_detail = True if need_detail is None else bool(need_detail(item))
            within_budget = detail_limit is None or fetched < detail_limit
            if wanted_detail and within_budget:
                detail = await api.job_profile_detail(
                    item.get("jobProfileIdentifier", "")
                )
                if detail is not None:
                    fetched += 1
        mapped = map_job_profile(item, detail)
        mapped.raw["detail_fetched"] = detail is not None
        results.append(mapped)

    logger.info(
        "Collected %d job profiles via API (%d details fetched)",
        len(results),
        fetched,
    )
    return results
