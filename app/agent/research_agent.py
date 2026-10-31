"""Research agent.

For each **new or changed** posting this builds one markdown document with the
critical facts plus a single AI-written summary line, saves it under
``research/<company>/<date>-<role>.md``, and renders the same content as a
neatly formatted Telegram message.

Change detection itself is pure hash comparison (no LLM). The model is used for
one summary sentence only, and if no model is configured the summary falls back
to a factual line built from the data — so nothing is ever invented.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.services.alerts import escape_markdown

logger = logging.getLogger(__name__)

RESEARCH_DIR = Path("research")

# Portal status -> (emoji, label)
STATUS_TAGS: dict[str, tuple[str, str]] = {
    "APPLIED": ("✅", "APPLIED"),
    "NOT_APPLIED": ("⏳", "TO APPLY"),
    "YET_TO_APPLY": ("⏳", "TO APPLY"),
    "SHORTLISTED": ("🎯", "SHORTLISTED"),
    "ASSESSMENT": ("📝", "ASSESSMENT"),
    "INTERVIEW": ("🗣", "INTERVIEW"),
    "OFFERED": ("🎉", "OFFERED"),
    "OFFER_ACCEPTED": ("🎉", "OFFER ACCEPTED"),
    "OFFER_REJECTED": ("❌", "OFFER REJECTED"),
    "REJECTED": ("❌", "REJECTED"),
    "WITHDRAWN": ("↩️", "WITHDRAWN"),
    "CLOSED_FOR_APPLICATIONS": ("🔒", "CLOSED"),
    "OPEN_FOR_APPLICATIONS": ("🟢", "OPEN"),
}

NEW_TAG = ("🆕", "NEW")
CHANGED_TAG = ("🔄", "UPDATED")


def _slug(value: str | None, fallback: str = "unknown") -> str:
    """Filesystem-safe slug."""
    if not value:
        return fallback
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return slug[:60] or fallback


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def status_tag(
    application_status: str | None, change_type: str | None = None
) -> tuple[str, str]:
    """Return ``(emoji, label)`` for the Telegram tag.

    An already-``APPLIED`` posting is tagged APPLIED even when it first
    appears, because that is the more useful fact. Otherwise a new posting is
    tagged NEW and an update UPDATED.
    """
    status = (application_status or "").strip().upper()
    if status == "APPLIED":
        return STATUS_TAGS["APPLIED"]
    if change_type == "new":
        return NEW_TAG
    if change_type == "changed":
        return CHANGED_TAG
    if status:
        return STATUS_TAGS.get(status, ("ℹ️", status))
    return ("ℹ️", "UNKNOWN")


def _days_left(deadline: dt.datetime | None, now: dt.datetime) -> int | None:
    deadline = _aware(deadline)
    if deadline is None:
        return None
    return (deadline - now).days


def _application_status(record: Any) -> str | None:
    raw = (getattr(record, "raw", None) or {}) if record is not None else {}
    return raw.get("application_status") or raw.get("job_profile_status")


def _facts(record: Any, now: dt.datetime) -> dict[str, Any]:
    """Critical facts, straight from the portal."""
    deadline = _aware(getattr(record, "deadline", None))
    return {
        "title": getattr(record, "title", None),
        "company": getattr(record, "company_name", None),
        "location": getattr(record, "location", None),
        "work_type": getattr(record, "work_type", None),
        "compensation": getattr(record, "compensation", None),
        "eligibility": getattr(record, "eligibility", None),
        "job_function": getattr(record, "job_function", None),
        "category": ((getattr(record, "raw", None) or {}).get("category")),
        "deadline": deadline,
        "days_left": _days_left(deadline, now),
        "first_seen": _aware(getattr(record, "first_seen_at", None)),
        "application_status": _application_status(record),
        "link": getattr(record, "apply_url", None),
    }


# -- AI summary ---------------------------------------------------------

_SUMMARY_SYSTEM = (
    "You write ONE sentence (max 25 words) describing why a job posting matters "
    "to a student, using only the facts given. No speculation, no invented "
    "details. If the facts are thin, say only what is given."
)


def _llm_summary(facts: Mapping[str, Any]) -> str | None:
    """One-sentence summary, or None if no model is reachable."""
    try:
        from app.services.llm import default_client

        client = default_client()
        if not client.configured:
            return None
        payload = {
            k: v for k, v in facts.items()
            if k in ("title", "company", "work_type", "location", "compensation",
                     "category", "eligibility")
        }
        text = client.complete_text(_SUMMARY_SYSTEM, str(payload))
        if not text:
            return None
        # Models sometimes wrap the answer in quotes or a "Summary:" prefix.
        text = text.strip().strip('"').strip()
        if text.lower().startswith("summary:"):
            text = text.split(":", 1)[1].strip()
        return " ".join(text.split())[:240] or None
    except Exception as exc:  # noqa: BLE001 - summary is never essential
        logger.debug("Summary generation skipped: %s", exc)
        return None


def _factual_summary(facts: Mapping[str, Any]) -> str:
    """Fallback summary built purely from stored facts."""
    bits: list[str] = []
    if facts.get("work_type"):
        bits.append(str(facts["work_type"]))
    if facts.get("job_function"):
        bits.append(str(facts["job_function"]))
    if facts.get("compensation"):
        bits.append(str(facts["compensation"]))
    if not bits:
        return "Posting collected from the Superset portal."
    return " · ".join(bits)[:240]


# -- document -----------------------------------------------------------

def build_document(
    record: Any,
    change_type: str,
    changes: Mapping[str, Any] | None = None,
    now: dt.datetime | None = None,
    use_model: bool = True,
) -> str:
    """Render the markdown document for a posting."""
    now = now or dt.datetime.now(dt.UTC)
    facts = _facts(record, now)
    emoji, label = status_tag(facts["application_status"], change_type)
    summary = _llm_summary(facts) if use_model else None
    summary = summary or _factual_summary(facts)

    lines: list[str] = [f"# {facts['title'] or 'Untitled role'}", ""]
    lines.append(f"**{facts['company'] or 'Unknown company'}**")
    lines.append("")

    lines.append(f"- **Tag:** {emoji} {label}")
    if facts["application_status"]:
        lines.append(f"- **Application status (portal):** {facts['application_status']}")
    if facts["work_type"]:
        lines.append(f"- **Type:** {facts['work_type']}")
    if facts["location"]:
        lines.append(f"- **Location:** {facts['location']}")
    if facts["compensation"]:
        lines.append(f"- **Compensation:** {facts['compensation']}")
    if facts["category"]:
        lines.append(f"- **Category:** {facts['category']}")
    if facts["eligibility"]:
        lines.append(f"- **Eligibility:** {facts['eligibility']}")
    else:
        lines.append("- **Eligibility:** unknown (not published)")
    if facts["deadline"]:
        days = facts["days_left"]
        when = facts["deadline"].date().isoformat()
        suffix = f" ({days} day(s) left)" if days is not None and days >= 0 else " (passed)"
        lines.append(f"- **Deadline:** {when}{suffix}")
    else:
        lines.append("- **Deadline:** unknown (not published)")
    if facts["first_seen"]:
        lines.append(f"- **First seen:** {facts['first_seen'].date().isoformat()}")
    lines.append(f"- **Detected:** {now.date().isoformat()}")

    if changes:
        lines.extend(["", "## What changed", ""])
        for field_name, pair in changes.items():
            old, new = pair.get("old"), pair.get("new")
            lines.append(
                f"- **{field_name.replace('_', ' ').title()}:** "
                f"`{old if old not in (None, '') else 'not set'}` → "
                f"`{new if new not in (None, '') else 'not set'}`"
            )

    lines.extend(["", "## Summary", "", summary, ""])

    description = (getattr(record, "description", None) or "").strip()
    if description:
        lines.extend(["## Job description", "", description[:6000], ""])

    if facts["link"]:
        lines.extend(["## Links", "", f"- Portal: {facts['link']}", ""])

    return "\n".join(lines)


def write_document(
    content: str,
    company: str | None,
    title: str | None,
    now: dt.datetime | None = None,
    root: Path | None = None,
) -> Path:
    """Write ``research/<company>/<date>-<role>.md`` and return its path.

    If a document for the same role on the same day already exists (e.g. the
    posting changed again), a ``-2``/``-3`` suffix is added rather than
    overwriting the earlier version.
    """
    now = now or dt.datetime.now(dt.UTC)
    base = root or RESEARCH_DIR
    folder = base / _slug(company, "unknown-company")
    folder.mkdir(parents=True, exist_ok=True)

    stem = f"{now.date().isoformat()}-{_slug(title, 'role')}"
    path = folder / f"{stem}.md"
    counter = 2
    while path.exists():
        path = folder / f"{stem}-{counter}.md"
        counter += 1

    path.write_text(content, encoding="utf-8")
    logger.info("Wrote research document %s", path)
    return path


# -- telegram -----------------------------------------------------------

def format_telegram(
    record: Any,
    change_type: str,
    changes: Mapping[str, Any] | None = None,
    document_path: Path | None = None,
    now: dt.datetime | None = None,
    reminder_days_left: int | None = None,
) -> str:
    """Render the neat Telegram message from the same facts.

    ``reminder_days_left`` turns the message into a deadline reminder, which
    keeps one renderer for every alert type.
    """
    now = now or dt.datetime.now(dt.UTC)
    facts = _facts(record, now)
    emoji, label = status_tag(facts["application_status"], change_type)
    if reminder_days_left is not None:
        # A deadline reminder leads with the clock, not the application status.
        emoji, label = "⏰", "DEADLINE REMINDER"

    title = escape_markdown(str(facts["title"] or "New posting"))
    lines = [f"{emoji} {label} · *{title}*"]

    company = facts.get("company")
    if company:
        lines.append(f"\U0001F3E2 {escape_markdown(str(company))}")

    meta: list[str] = []
    if facts.get("work_type"):
        meta.append(escape_markdown(str(facts["work_type"])))
    if facts.get("location"):
        meta.append(escape_markdown(str(facts["location"])))
    if meta:
        lines.append("\U0001F4CD " + " · ".join(meta))

    if facts.get("compensation"):
        lines.append(f"\U0001F4B0 {escape_markdown(str(facts['compensation']))}")
    if facts.get("eligibility"):
        lines.append(f"\U0001F393 {escape_markdown(str(facts['eligibility']))}")

    if facts.get("deadline"):
        days = facts["days_left"]
        when = facts["deadline"].date().isoformat()
        if days is not None and days < 0:
            lines.append(f"⏰ Deadline passed ({when})")
        elif days == 0:
            lines.append(f"⏰ Closes *today* ({when})")
        elif days is not None:
            lines.append(f"⏰ Closes in *{days}* day(s) · {when}")
        else:
            lines.append(f"⏰ {when}")

    if changes:
        changed_fields = list(changes)[:3]
        lines.append(
            "\U0001F504 Updated: "
            + ", ".join(escape_markdown(f.replace("_", " ")) for f in changed_fields)
        )

    if facts.get("link"):
        lines.append(str(facts["link"]))
    if document_path is not None:
        lines.append(f"\U0001F4C4 {document_path}")

    return "\n".join(lines)