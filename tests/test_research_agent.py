"""Tests for the research agent: documents, tags and Telegram formatting."""

from __future__ import annotations

import datetime as dt

from app.agent.research_agent import (
    build_document,
    format_telegram,
    status_tag,
    write_document,
)


class FakeRecord:
    """Stand-in for an Opportunity row."""

    def __init__(self, **kwargs):
        self.title = kwargs.get("title", "Backend Intern")
        self.company_name = kwargs.get("company_name", "Zensar Technologies")
        self.location = kwargs.get("location", "Pune")
        self.work_type = kwargs.get("work_type", "Internship")
        self.compensation = kwargs.get("compensation", "Effective 90,000 INR (month)")
        self.eligibility = kwargs.get("eligibility", "Eligible")
        self.job_function = kwargs.get("job_function", "Engineering")
        self.description = kwargs.get("description", "Work on backend services.")
        self.apply_url = kwargs.get("apply_url", "https://app.joinsuperset.com/x")
        self.deadline = kwargs.get("deadline")
        self.first_seen_at = kwargs.get("first_seen_at")
        self.raw = kwargs.get("raw", {"application_status": "YET_TO_APPLY",
                                      "category": "Tier 2"})


NOW = dt.datetime(2026, 10, 14, 12, 0, tzinfo=dt.timezone.utc)
DEADLINE = dt.datetime(2026, 10, 20, 23, 59, tzinfo=dt.timezone.utc)


# --- status tags -------------------------------------------------------

def test_new_posting_is_tagged_new():
    assert status_tag("YET_TO_APPLY", "new") == ("🆕", "NEW")


def test_applied_status_beats_new_tag():
    """You already applied: that is the more useful fact to show."""
    assert status_tag("APPLIED", "new") == ("✅", "APPLIED")
    assert status_tag("APPLIED", "changed") == ("✅", "APPLIED")


def test_changed_posting_is_tagged_updated():
    assert status_tag("YET_TO_APPLY", "changed") == ("🔄", "UPDATED")


def test_status_tag_reflects_portal_status():
    assert status_tag("APPLIED", None)[1] == "APPLIED"
    assert status_tag("OFFERED", None)[0] == "🎉"
    assert status_tag("REJECTED", None)[0] == "❌"
    assert status_tag("CLOSED_FOR_APPLICATIONS", None)[0] == "🔒"


def test_status_tag_falls_back_when_unknown():
    assert status_tag(None, None) == ("ℹ️", "UNKNOWN")
    assert status_tag("WEIRD_STATUS", None) == ("ℹ️", "WEIRD_STATUS")


# --- document ----------------------------------------------------------

def test_document_contains_critical_facts():
    record = FakeRecord(deadline=DEADLINE, first_seen_at=NOW)
    doc = build_document(record, "new", now=NOW, use_model=False)

    assert "# Backend Intern" in doc
    assert "Zensar Technologies" in doc
    assert "Pune" in doc
    assert "90,000" in doc
    assert "Eligible" in doc
    assert "2026-10-20" in doc          # deadline
    assert "6 day(s) left" in doc
    assert "Tier 2" in doc


def test_document_marks_unknown_fields_rather_than_guessing():
    record = FakeRecord(deadline=None, eligibility=None)
    doc = build_document(record, "new", now=NOW, use_model=False)
    # Markdown-bold labels, so match the text after the bold marker.
    assert "**Deadline:** unknown (not published)" in doc
    assert "**Eligibility:** unknown (not published)" in doc


def test_document_lists_what_changed():
    record = FakeRecord(deadline=DEADLINE)
    changes = {"deadline": {"old": dt.datetime(2026, 10, 18, tzinfo=dt.timezone.utc),
                            "new": DEADLINE}}
    doc = build_document(record, "changed", changes=changes, now=NOW, use_model=False)
    assert "## What changed" in doc
    assert "Deadline" in doc
    assert "2026-10-18" in doc and "2026-10-20" in doc


def test_document_falls_back_to_factual_summary_without_model():
    doc = build_document(FakeRecord(), "new", now=NOW, use_model=False)
    summary = doc.split("## Summary", 1)[1].split("## Job description")[0]
    assert "Internship" in summary


def test_write_document_path_layout(tmp_path):
    path = write_document("body", "Zensar Technologies", "Backend Intern",
                          now=NOW, root=tmp_path)
    assert path.parent.name == "zensar-technologies"
    assert path.name == "2026-10-14-backend-intern.md"
    assert path.read_text() == "body"


def test_write_document_does_not_overwrite_same_day(tmp_path):
    first = write_document("v1", "Acme", "Intern", now=NOW, root=tmp_path)
    second = write_document("v2", "Acme", "Intern", now=NOW, root=tmp_path)
    assert first.name == "2026-10-14-intern.md"
    assert second.name == "2026-10-14-intern-2.md"
    assert first.read_text() == "v1"


def test_write_document_slugs_unsafe_names(tmp_path):
    path = write_document("b", "Zensar / Tech (P) Ltd", "C++ / Java Intern!",
                          now=NOW, root=tmp_path)
    assert path.parent.name == "zensar-tech-p-ltd"
    assert "/" not in path.name


# --- telegram ----------------------------------------------------------

def test_telegram_message_is_neat_and_tagged():
    record = FakeRecord(deadline=DEADLINE)
    message = format_telegram(record, "new", now=NOW)

    assert message.startswith("🆕 NEW · *Backend Intern*")
    assert "🏢 Zensar Technologies" in message
    assert "📍 Internship · Pune" in message
    # Parentheses are Markdown-significant, so they are escaped.
    assert "💰 Effective 90,000 INR \\(month\\)" in message
    assert "⏰ Closes in *6* day(s) · 2026-10-20" in message


def test_telegram_reports_applied_status():
    record = FakeRecord(raw={"application_status": "APPLIED", "category": "Tier 2"})
    message = format_telegram(record, "new", now=NOW)
    assert message.startswith("✅ APPLIED")


def test_new_tag_used_when_not_applied():
    record = FakeRecord(raw={"application_status": "YET_TO_APPLY"})
    assert format_telegram(record, "new", now=NOW).startswith("🆕 NEW")


def test_telegram_says_deadline_passed():
    past = NOW - dt.timedelta(days=3)
    message = format_telegram(FakeRecord(deadline=past), "new", now=NOW)
    assert "Deadline passed" in message


def test_telegram_closes_today():
    today = NOW
    message = format_telegram(FakeRecord(deadline=today), "new", now=NOW)
    assert "Closes *today*" in message


def test_telegram_escapes_markdown_specials():
    record = FakeRecord(title="SDE_1 (IT+PBC)", company_name="A*B Corp")
    message = format_telegram(record, "new", now=NOW)
    assert "SDE\\_1 \\(IT\\+PBC\\)" in message
    assert "A\\*B Corp" in message


def test_telegram_mentions_changed_fields():
    message = format_telegram(
        FakeRecord(deadline=DEADLINE), "changed",
        changes={"deadline": {"old": "x", "new": DEADLINE}}, now=NOW,
    )
    assert "🔄 Updated: deadline" in message
    assert message.startswith("🔄 UPDATED")


def test_telegram_includes_document_path(tmp_path):
    path = tmp_path / "doc.md"
    message = format_telegram(FakeRecord(), "new", document_path=path, now=NOW)
    assert "📄" in message and "doc.md" in message


def test_naive_deadline_is_handled(tmp_path):
    """SQLite returns naive datetimes; the formatter must not crash."""
    naive = dt.datetime(2026, 10, 20, 23, 59)
    message = format_telegram(FakeRecord(deadline=naive), "new", now=NOW)
    assert "2026-10-20" in message

def test_telegram_reminder_variant():
    record = FakeRecord(deadline=DEADLINE)
    message = format_telegram(record, None, now=NOW, reminder_days_left=2)
    assert message.startswith("⏰ DEADLINE REMINDER")
    assert "Closes in *6* day(s)" in message
