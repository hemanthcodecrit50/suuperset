"""Tests for alert message formatting and Markdown safety."""

from __future__ import annotations

from app.services.alerts import escape_markdown, format_alert_message


def test_escape_markdown_handles_job_title_characters():
    # Real-world titles contain these and break Telegram's parser unescaped.
    raw = "SDE_1 (IT+PBC) — Java Backend *intern*"
    escaped = escape_markdown(raw)
    assert "\\_" in escaped
    assert "\\*" in escaped
    assert "\\(" in escaped and "\\)" in escaped


def test_format_message_includes_all_fields():
    payload = {
        "title": "Application Developer - Associate",
        "company_name": "Thoughtworks",
        "location": "PAN India",
        "compensation": "Effective 90,000 INR (month)",
        "deadline": "2026-10-20 23:59",
        "change_type": "deadline_changed",
        "relevance_reason": "Matches your skills",
        "next_action": "Apply before the new deadline",
        "url": "https://app.joinsuperset.com/students/jobprofiles",
    }
    message = format_alert_message(payload)
    for expected in ("Thoughtworks", "PAN India", "Apply before the new deadline",
                     "https://app.joinsuperset.com"):
        assert expected in message
    # Underscores are escaped, so the raw value does not appear verbatim.
    assert "deadline\\_changed" in message
    assert message.startswith("*")


def test_format_message_skips_missing_fields():
    message = format_alert_message({"title": "Something new"})
    assert message.strip() == "*Something new*"


def test_format_message_never_leaves_unbalanced_markdown():
    """Every dynamic value must be escaped so Markdown stays balanced."""
    payload = {
        "title": "Role_with_underscores",
        "company_name": "A*B*C Corp",
        "url": "https://example.com/x",
    }
    message = format_alert_message(payload)
    # Only the intentional bold markers are unescaped asterisks.
    assert message.replace("\\*", "").count("*") == 2
    assert "A\\*B\\*C Corp" in message
    assert "Role\\_with\\_underscores" in message