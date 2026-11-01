"""Pull dated events (tests, interviews, deadlines) out of a notice, checked against its text."""

from __future__ import annotations

import datetime as dt
import re

from app.services.llm import LlmClient
from app.services.notices import company_in_cell, mentioned_company

MAX_EVENTS_PER_NOTICE = 8
MAX_NOTES_CHARS = 240
TABLE_DATE_PATTERN = re.compile(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})")
MONTH_NAMES = (
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
)

SCHEDULE_SYSTEM = """You read a college placement notice and list the dated events in it.

Hard rules:
- Use only what the notice says. Never guess a date, time or venue.
- Skip events without a clear date.
- "what" is a short label in the notice's own words, e.g. "Online assessment".
- "company" is the company the event belongs to, as written, or null if the notice is about one company.
- An event on two days ("10th & 11th Nov") is two events.

Respond with a JSON object:
{"events": [{"what": "...", "company": "... or null", "date": "YYYY-MM-DD",
             "time": "10:00 AM or null", "venue": "place or link, or null",
             "notes": "what to bring or do, or null"}]}"""


def extract_schedule(
    text: str,
    llm: LlmClient,
    *,
    year_hint: int,
    companies: list[str],
    default_company: str,
) -> list[dict] | None:
    """Dated events for the student's companies, or None when the model gave no answer."""
    answer = llm.complete_json(
        SCHEDULE_SYSTEM, f"Notice published in {year_hint}:\n\n{text[:8000]}"
    )
    if answer is None:
        return None
    events: list[dict] = []
    for item in answer.get("events") or []:
        event = _grounded_event(item, text)
        if event is None:
            continue
        named = str(item.get("company") or "").strip()
        # Events of other companies in a shared notice are not the student's business
        event["company"] = (
            mentioned_company(named, companies) if named else default_company
        )
        if event["company"] is not None:
            events.append(event)
    return events[:MAX_EVENTS_PER_NOTICE]


def parse_schedule_table(text: str, companies: list[str]) -> list[dict]:
    """Rows of a timetable notice ("date | company | process") that concern the student.

    Read with rules, not the AI: the table already says exactly what happens when.
    A row without a date continues the date above it, as merged table cells do.
    """
    events: list[dict] = []
    current_date: dt.date | None = None
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.split("|") if cell.strip()]
        date_cells = [_table_date(cell) for cell in cells]
        found = next((date for date in date_cells if date is not None), None)
        if found is not None:
            current_date = found
        others = [cell for cell, date in zip(cells, date_cells) if date is None]
        if current_date is None or not others:
            continue
        company = company_in_cell(others[0], companies)
        if company is None:
            continue
        events.append(
            {
                "what": others[1] if len(others) > 1 else "Placement process",
                "company": company,
                "date": current_date.isoformat(),
                "time": None,
                "venue": None,
                "notes": "From the tentative weekly schedule; it may change.",
                "tentative": True,
            }
        )
    return events


def _table_date(cell: str) -> dt.date | None:
    match = TABLE_DATE_PATTERN.fullmatch(cell)
    if match is None:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def _grounded_event(item: object, text: str) -> dict | None:
    if not isinstance(item, dict):
        return None
    date = _parse_date(item.get("date"))
    if date is None or not is_date_in_text(date, text):
        return None
    # Notices often write "Venue:* Hall 2", so drop leading bullets and colons
    venue = str(item.get("venue") or "").strip().lstrip("*•:-– ").strip()
    time = str(item.get("time") or "").strip()
    return {
        "what": str(item.get("what") or "Event").strip()[:120],
        "date": date.isoformat(),
        "time": time if time and _is_time_in_text(time, text) else None,
        "venue": venue if venue and _squash(venue) in _squash(text) else None,
        "notes": _shorten(str(item.get("notes") or "").strip(), MAX_NOTES_CHARS) or None,
    }


def _shorten(text: str, limit: int) -> str:
    # Cut at a word boundary so notes never end mid-word
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;") + "…"


def is_date_in_text(date: dt.date, text: str) -> bool:
    """True when the notice writes this day and month in any common form."""
    lowered = text.lower()
    day = date.day
    month_name = MONTH_NAMES[date.month - 1]
    has_day = re.search(rf"(?<!\d){day}(?:st|nd|rd|th)?(?!\d)", lowered) is not None
    has_month_name = month_name in lowered
    has_numeric = re.search(
        rf"(?<!\d)0?{day}[/\-.]0?{date.month}(?!\d)|(?<!\d)0?{date.month}[/\-.]0?{day}(?!\d)"
        rf"|{date.year}-{date.month:02d}-{day:02d}",
        lowered,
    )
    return bool(has_numeric) or (has_day and has_month_name)


def _is_time_in_text(time: str, text: str) -> bool:
    hour = re.match(r"\s*(\d{1,2})", time)
    return (
        hour is not None
        and re.search(rf"(?<!\d){int(hour.group(1))}(?!\d)", text) is not None
    )


def _parse_date(value: object) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        return None


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())
