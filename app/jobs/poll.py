"""The polling job: collect, detect changes, persist and queue alerts.

Designed to be safe to run from a systemd timer or GitHub Actions:
- a file lock prevents overlapping runs;
- transient network failures are retried with exponential backoff;
- every run is recorded in ``poll_runs`` for health reporting;
- a heartbeat alert fires when no successful poll has happened recently.

Run with::

    python -m app.jobs.poll
"""

from __future__ import annotations

import asyncio
import datetime as dt
import errno
import fcntl
import logging
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

from sqlalchemy import func, select

from app.agent.research_agent import build_document, format_telegram, write_document
from app.collector.api import SupersetApi
from app.collector.browser import BrowserSession, SessionExpiredError
from app.collector.change_detector import (
    CHANGED,
    NEW,
    enqueue_alert,
    upsert_announcement,
    upsert_opportunity,
)
from app.collector.job_profiles import collect_job_profiles
from app.collector.mappers import collect_announcements_api, collect_job_profiles_api
from app.collector.notifications import collect_announcements
from app.config import Settings, get_settings
from app.db import session_scope
from app.models import PollRun, Source
from app.services.alerts import CHANNEL_EMAIL, CHANNEL_TELEGRAM, deliver_pending

logger = logging.getLogger(__name__)


@dataclass
class PollResult:
    status: str
    items_seen: int = 0
    items_new: int = 0
    items_changed: int = 0
    items_filtered: int = 0
    items_enriched: int = 0
    items_documented: int = 0
    items_briefed: int = 0
    alerts_queued: int = 0
    error: str | None = None
    details: dict = field(default_factory=dict)


class PollLocked(RuntimeError):
    """Raised when another poll is already running."""


@contextmanager
def file_lock(path: str) -> Iterator[None]:
    """Non-blocking exclusive lock to prevent overlapping runs."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    handle = open(path, "w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise PollLocked(f"Another poll is running (lock: {path})") from exc
            raise
        handle.write(str(os.getpid()))
        handle.flush()
        yield
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()


def _channel(settings: Settings) -> str:
    if settings.telegram_configured:
        return CHANNEL_TELEGRAM
    if settings.email_configured:
        return CHANNEL_EMAIL
    return "dashboard"


def _stored_details(session) -> dict:
    """Snapshot stored JD/CTC/eligibility so it is not wiped when skipped."""
    from sqlalchemy import select as sa_select

    from app.models import Opportunity as _Opportunity

    rows = session.execute(
        sa_select(
            _Opportunity.external_id,
            _Opportunity.description,
            _Opportunity.compensation,
            _Opportunity.eligibility,
            _Opportunity.job_function,
            _Opportunity.skills,
        )
    ).all()
    return {
        row[0]: {
            "description": row[1],
            "compensation": row[2],
            "eligibility": row[3],
            "job_function": row[4],
            "skills": row[5],
        }
        for row in rows
    }


def _detail_predicate(settings: Settings):
    """Return a predicate deciding which jobs need a full-JD fetch.

    Detail is fetched only for roles we have never detailed before, so the
    request count stays near the number of *new* postings rather than the size
    of the whole list on every poll.
    """
    from sqlalchemy import select as sa_select

    from app.db import session_scope as _scope
    from app.models import Opportunity as _Opportunity

    with _scope() as session:
        rows = session.execute(
            sa_select(
                _Opportunity.external_id, _Opportunity.detail_fetched_at
            )
        ).all()
        already_detailed = {
            external_id for external_id, fetched_at in rows if fetched_at is not None
        }

    def need_detail(item: dict) -> bool:
        return item.get("jobProfileIdentifier") not in already_detailed

    known_count = len(rows)
    logger.info(
        "Detail fetches limited to unseen roles (%d already detailed of %d known).",
        len(already_detailed),
        known_count,
    )
    return need_detail


async def _collect(settings: Settings) -> tuple[list, list]:
    """Collect announcements and job profiles.

    Prefers the portal's JSON API (fast, structured, stable ids) and falls back
    to DOM scraping if the API is unavailable. Either way the caller receives
    the same dataclasses.

    Returns ``(announcements, opportunities, errors)``. Each source is fetched
    independently so one failing endpoint does not discard the other's data;
    ``errors`` records what went wrong so the run is never reported as a clean
    success when collection was actually incomplete.
    """
    announcements: list = []
    opportunities: list = []
    errors: list[str] = []

    async with BrowserSession(settings=settings, headless=True) as session:
        if settings.collector_mode != "dom":
            api = None
            try:
                api = await SupersetApi.from_context(session._context, settings)
            except Exception as exc:  # noqa: BLE001
                message = f"API authentication failed: {exc}"
                logger.error(message)
                if settings.collector_mode == "api":
                    raise
                errors.append(message)

            if api is not None:
                # Independent fetches: keep whatever succeeds.
                try:
                    announcements = await collect_announcements_api(api)
                except Exception as exc:  # noqa: BLE001
                    message = f"announcements fetch failed: {exc}"
                    logger.error(message)
                    errors.append(message)

                try:
                    opportunities = await collect_job_profiles_api(
                        api,
                        include_detail=settings.fetch_job_details,
                        detail_limit=settings.detail_limit or None,
                        status=settings.job_status_filter or None,
                        need_detail=(
                            _detail_predicate(settings)
                            if settings.fetch_job_details
                            else None
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    message = f"job profiles fetch failed: {exc}"
                    logger.error(message)
                    errors.append(message)

                if not errors:
                    logger.info("Collection completed via JSON API.")
                    return announcements, opportunities, errors

                # Partial: do not fall back to DOM, whose selectors are
                # placeholders that would add noise and hide the failure.
                logger.warning("Partial API collection: %s", "; ".join(errors))
                return announcements, opportunities, errors

        # DOM fallback (only meaningful when explicitly selected)
        ann_page = await session.goto(
            settings.superset_base_url.rstrip("/") + settings.superset_notifications_path,
            expect_app=True,
        )
        announcements = await collect_announcements(ann_page, max_scrolls=10)

        job_page = await session.goto(
            settings.superset_base_url.rstrip("/") + settings.superset_job_profiles_path,
            expect_app=True,
        )
        opportunities = await collect_job_profiles(
            job_page,
            search=settings.job_search or None,
            status=settings.job_status_filter or None,
            tab=settings.job_tab or None,
            load_more_scrolls=15,
        )

    return announcements, opportunities, errors


def _score_and_gate(
    session,
    record,
    preferences,
    change: str,
) -> tuple[bool, list[str]]:
    """Score an opportunity and decide whether it deserves an alert.

    Returns ``(should_alert, reasons)``. Low-relevance items are still stored
    and visible in the dashboard — they are simply not pushed to the user.
    """
    from app.services.matching import score_opportunity

    payload = {
        "title": record.title,
        "role": record.role,
        "job_function": record.job_function,
        "work_type": record.work_type,
        "location": record.location,
        "compensation": record.compensation,
        "description": record.description,
        "skills": record.skills or [],
    }
    match = score_opportunity(payload, preferences.as_match_kwargs())

    record.relevance_score = match.score
    record.relevance_reasons = match.reasons
    record.relevance_unknown = match.unknown
    record.eligible = match.hard_constraints_met

    reasons = list(match.reasons)
    if match.hard_constraints_met is False:
        reasons.append("You are marked not eligible for this role")
    if record.deadline:
        reasons.append(f"Deadline: {record.deadline.date().isoformat()}")

    # Hard eligibility rules and tracked applications always notify.
    if match.hard_constraints_met is False:
        return True, reasons
    if preferences.always_notify_company(record.company_name):
        reasons.append(f"You follow {record.company_name}")
        return True, reasons
    if change == CHANGED and preferences.always_alert_changes:
        return True, reasons

    if not preferences.is_configured():
        # No preferences set: fall back to alerting on everything new.
        return True, reasons
    return match.score >= preferences.min_alert_score, reasons


def _field_changes(session, record) -> dict:
    """Old-vs-new values from the version row written for this change."""
    from sqlalchemy import select

    from app.models import OpportunityVersion

    version = session.scalar(
        select(OpportunityVersion)
        .where(OpportunityVersion.opportunity_id == record.id)
        .order_by(OpportunityVersion.id.desc())
        .limit(1)
    )
    if version is None:
        return {}

    # Only fields the version row actually stores can be compared; comparing
    # against columns that were never versioned would report false changes.
    old = {
        "title": version.title,
        "description": version.description,
        "eligibility": version.eligibility,
        "deadline": version.deadline,
        "skills": version.skills,
    }
    new = {field: getattr(record, field, None) for field in old}

    differences: dict[str, dict] = {}
    for field_name in old:
        if _normalise(old.get(field_name)) != _normalise(new.get(field_name)):
            differences[field_name] = {
                "old": old.get(field_name),
                "new": new.get(field_name),
            }
    return differences


def _normalise(value):
    if isinstance(value, dt.datetime):
        return value.astimezone(dt.timezone.utc).isoformat()
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, (list, tuple)):
        return list(value)
    return value


def _persist(
    session,
    settings: Settings,
    announcements,
    opportunities,
    *,
    queue_alerts: bool = True,
) -> PollResult:
    """Upsert collected items and (optionally) queue alerts.

    ``queue_alerts`` is disabled on the very first successful run: that run
    discovers the entire backlog, and alerting on it would bury the user. An
    ``max_alerts_per_run`` cap protects against floods on later runs too.
    """
    from app.services.preferences import load_preferences

    channel = _channel(settings)
    result = PollResult(status="success")
    result.items_seen = len(announcements) + len(opportunities)
    suppressed = 0
    cap = max(0, settings.max_alerts_per_run)
    preferences = load_preferences(session)
    stored_details = _stored_details(session)

    def maybe_queue(**kwargs) -> None:
        nonlocal suppressed
        if not queue_alerts:
            suppressed += 1
            return
        if cap and result.alerts_queued >= cap:
            suppressed += 1
            return
        if enqueue_alert(session, channel=channel, **kwargs):
            result.alerts_queued += 1

    for ann in announcements:
        record, change = upsert_announcement(session, ann.__dict__)
        if change == NEW:
            result.items_new += 1
        elif change == CHANGED:
            result.items_changed += 1
        if change in (NEW, CHANGED):
            maybe_queue(
                change_type=change,
                dedup_key=f"announcement:{record.id}:{change}:{record.content_hash}",
                announcement_id=record.id,
                payload={
                    "title": record.title,
                    "change_type": change,
                    "url": record.url,
                },
            )

    for opp in opportunities:
        # A skipped detail fetch must not erase data we already have.
        if not opp.raw.get("detail_fetched"):
            stored = stored_details.get(opp.external_id)
            if stored:
                for field_name in ("description", "compensation", "eligibility",
                                   "job_function"):
                    if not getattr(opp, field_name, None):
                        setattr(opp, field_name, stored.get(field_name))
                if not opp.skills and stored.get("skills"):
                    opp.skills = stored["skills"]

        record, change = upsert_opportunity(session, opp.__dict__)
        # First-time JD enrichment makes the hash change, but that is new data,
        # not a real update the user needs to hear about.
        was_detailed = record.detail_fetched_at is not None
        if opp.raw.get("detail_fetched"):
            record.detail_fetched_at = dt.datetime.now(dt.timezone.utc)
        if change == NEW:
            result.items_new += 1
        elif change == CHANGED:
            result.items_changed += 1
            if not was_detailed:
                result.items_enriched += 1
                change = "enriched"

        should_alert = change in (NEW, CHANGED)
        reasons: list[str] = []
        if should_alert:
            should_alert, reasons = _score_and_gate(session, record, preferences, change)

        changes: dict = {}
        if change == CHANGED:
            changes = _field_changes(session, record)

        if change in (NEW, CHANGED) and should_alert:
            # Research agent: build the document, then format the message from
            # the same facts so the file and the alert never disagree.
            document = build_document(record, change, changes=changes)
            brief = None
            if queue_alerts and result.items_briefed < settings.agent_max_briefs_per_run:
                brief = _agent_brief(session, settings, record, change, changes)
                if brief:
                    document += "\n" + brief
                    result.items_briefed += 1
            document_path = write_document(
                document, record.company_name, record.title
            )
            message = format_telegram(
                record,
                change,
                changes=changes,
                document_path=document_path,
                brief=brief,
            )
            maybe_queue(
                change_type=change,
                dedup_key=f"opportunity:{record.id}:{change}:{record.content_hash}",
                opportunity_id=record.id,
                payload={
                    # Pre-rendered: skips the generic field formatting.
                    "message": message,
                    "title": record.title,
                    "change_type": change,
                    "url": record.apply_url,
                },
            )
            result.items_documented += 1
        elif change == NEW:
            # Below threshold: stored and scored, but not pushed.
            result.items_filtered += 1

    session.flush()
    if suppressed:
        logger.warning(
            "Suppressed %d alerts (queue_alerts=%s, cap=%d).",
            suppressed,
            queue_alerts,
            cap,
        )
        result.details["alerts_suppressed"] = suppressed
    if result.items_briefed:
        result.details["agent_briefs"] = result.items_briefed
    if result.items_filtered:
        result.details["below_relevance_threshold"] = result.items_filtered
        logger.info(
            "%d new opportunities below the alert threshold (min_alert_score=%s).",
            result.items_filtered,
            preferences.min_alert_score,
        )
    return result


def _agent_brief(session, settings: Settings, record, change: str, changes: dict) -> str:
    """Run the agent graph for one item; a failure must never stop collection."""
    from app.agent.runner import analyze_opportunity, default_deps

    if not settings.agent_enabled:
        return ""
    # Flush first so the savepoint below only covers the agent's own writes
    session.flush()
    try:
        with session.begin_nested():
            return analyze_opportunity(
                session, record, change, changes, deps=default_deps(settings)
            ).markdown
    except Exception:  # noqa: BLE001 - the agent is an add-on to collection
        logger.exception("Agent brief failed for opportunity %s", record.id)
        return ""


def _next_action_for(record) -> str | None:
    if record.deadline:
        return f"Apply before {record.deadline.date().isoformat()}"
    return "Review and apply"


async def _run_once(settings: Settings) -> PollResult:
    tot = 0
    last_error: Exception | None = None
    # The first successful run only seeds the database; alerting on the whole
    # historical backlog would be noise, so it is suppressed.
    is_first_run = not _has_previous_success()
    for attempt in range(1, settings.poll_max_retries + 1):
        try:
            with session_scope() as session:
                run = PollRun(status="running")
                session.add(run)
                session.flush()
                run_id = run.id

            announcements, opportunities, errors = await _collect(settings)

            with session_scope() as session:
                result = _persist(
                    session,
                    settings,
                    announcements,
                    opportunities,
                    queue_alerts=not (
                        is_first_run and settings.suppress_alerts_on_first_run
                    ),
                )
                if errors:
                    # Persist whatever succeeded, but never report a clean
                    # success when collection was incomplete.
                    result.status = "partial"
                    result.error = "; ".join(errors)[:2000]
                    result.details["errors"] = errors
                _finalize_run(session, run_id, result)

            # Delivery is handled by the separate alert worker (app.main deliver)
            # so a Telegram/SMTP outage cannot fail the collection run.
            return result
        except SessionExpiredError as exc:
            logger.error("Session expired: %s", exc)
            with session_scope() as session:
                _finalize_run(
                    session, _latest_run_id(session), PollResult(status="failed", error=str(exc))
                )
            raise
        except Exception as exc:  # noqa: BLE001 - retry transient failures
            last_error = exc
            tot = attempt
            delay = settings.poll_retry_base_seconds * (2 ** (attempt - 1))
            logger.warning(
                "Poll attempt %d/%d failed: %s (retry in %.1fs)",
                attempt,
                settings.poll_max_retries,
                exc,
                delay,
            )
            if attempt < settings.poll_max_retries:
                await asyncio.sleep(delay)
            else:
                # Final attempt: close out the run row so health reporting
                # does not show it stuck in 'running' forever.
                with session_scope() as session:
                    _finalize_run(
                        session,
                        _latest_run_id(session),
                        PollResult(status="failed", error=str(exc)[:2000]),
                    )

    raise RuntimeError(f"Poll failed after {tot} attempts") from last_error


def _latest_run_id(session) -> int | None:
    return session.scalar(select(func.max(PollRun.id)))


def _has_previous_success() -> bool:
    """True if at least one successful poll has already been recorded."""
    with session_scope() as session:
        return (
            session.scalar(
                select(func.count(PollRun.id)).where(PollRun.status == "success")
            )
            or 0
        ) > 0


def _finalize_run(session, run_id: int | None, result: PollResult) -> None:
    if run_id is None:
        return
    run = session.get(PollRun, run_id)
    if run is None:
        return
    run.finished_at = dt.datetime.now(dt.timezone.utc)
    run.status = result.status
    run.items_seen = result.items_seen
    run.items_new = result.items_new
    run.items_changed = result.items_changed
    run.alerts_queued = result.alerts_queued
    run.notes = {**(result.details or {}), "items_filtered": result.items_filtered}
    run.error = result.error


def check_heartbeat(settings: Settings | None = None) -> bool:
    """Warn when no successful poll has occurred recently.

    Returns ``True`` if a heartbeat warning was raised.
    """
    settings = settings or get_settings()
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(
        hours=settings.heartbeat_max_age_hours
    )
    with session_scope() as session:
        last_success = session.scalar(
            select(func.max(PollRun.finished_at)).where(
                PollRun.status == "success", PollRun.finished_at >= cutoff
            )
        )
        if last_success is not None:
            return False
        logger.error(
            "Heartbeat: no successful poll since %s", cutoff.isoformat()
        )
        enqueue_alert(
            session,
            channel=_channel(settings),
            change_type="heartbeat",
            dedup_key=f"heartbeat:{cutoff.date().isoformat()}",
            payload={
                "title": "Placement collector heartbeat",
                "change_type": "heartbeat",
                "next_action": "Check the poller and reauthenticate if needed.",
            },
        )
    return True


def _default_source(settings: Settings) -> dict:
    return {
        "name": "superset",
        "base_url": settings.superset_base_url,
        "login_url": settings.superset_login_url,
        "enabled": True,
    }


def ensure_source(settings: Settings | None = None) -> None:
    """Ensure the Superset source row exists."""
    settings = settings or get_settings()
    with session_scope() as session:
        existing = session.scalar(select(Source).where(Source.name == "superset"))
        if existing is None:
            session.add(Source(**_default_source(settings)))


async def run_poll(settings: Settings | None = None) -> PollResult:
    settings = settings or get_settings()
    ensure_source(settings)
    with file_lock(settings.poll_lock_file):
        return await _run_once(settings)


def main() -> int:
    logging.basicConfig(
        level=get_settings().log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        result = asyncio.run(run_poll())
    except PollLocked as exc:
        logger.warning("%s", exc)
        return 0
    except Exception as exc:  # noqa: BLE001 - top-level job failure
        logger.exception("Poll run failed: %s", exc)
        return 1
    logger.info(
        "Poll complete: seen=%d new=%d changed=%d alerts=%d",
        result.items_seen,
        result.items_new,
        result.items_changed,
        result.alerts_queued,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
