"""Tests for rendering a brief as a memo with footnote citations."""

from __future__ import annotations

from app.web.brief_html import brief_sources, brief_to_html

BRIEF = """### Verdict
Think carefully.

### Key facts
- Deadline: 11 Nov 2026
- Pay: 13.3 LPA

### Company snapshot
- Cohesity makes data software ([source](https://en.wikipedia.org/wiki/Cohesity))
- Official site ([source](https://www.cohesity.com/))
"""


def test_sources_are_numbered_in_order_of_appearance():
    assert [source.domain for source in brief_sources(BRIEF)] == [
        "en.wikipedia.org",
        "cohesity.com",
    ]


def test_source_links_become_numbered_citations():
    assert ">[2]</a></sup>" in brief_to_html(BRIEF)


def test_key_facts_become_a_ledger():
    assert '<span class="ledger-key">Pay</span>' in brief_to_html(BRIEF)


def test_sections_are_tagged_for_styling():
    assert 'class="memo-section verdict"' in brief_to_html(BRIEF)


def test_html_in_the_brief_is_escaped():
    assert "<script>" not in brief_to_html("### Verdict\n<script>alert(1)</script>")
