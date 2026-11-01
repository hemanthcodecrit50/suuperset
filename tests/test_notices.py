"""Tests for notices, company matching, schedules and JD documents."""

from __future__ import annotations

import datetime as dt
import io
import zipfile

import pytest
from agent_helpers import make_settings

from app.collector.documents import document_text, is_likely_jd
from app.collector.mappers import NOTICE_CATEGORY, map_job_profile, map_notice
from app.models import Announcement, Application, Opportunity, Source
from app.services import notices
from app.services.schedule import (
    extract_schedule,
    is_date_in_text,
    parse_schedule_table,
)

NOTICE_TEXT = (
    "Cohesity Online Assessment\n"
    "The online test is on 24th November 2026 at 10:00 AM in the CSE Seminar Hall.\n"
    "Carry your college ID card."
)


class ScheduleLlm:
    configured = True

    def __init__(self, answer):
        self.answer = answer

    def complete_json(self, system, user, timeout=0):
        return self.answer


@pytest.fixture()
def source(session):
    record = Source(name="superset", base_url="https://app.joinsuperset.com")
    session.add(record)
    session.flush()
    return record


def _posting(session, source, company, status="APPLIED", external_id="job-1"):
    record = Opportunity(
        source_id=source.id,
        external_id=external_id,
        title="Role",
        company_name=company,
        raw={"application_status": status},
    )
    session.add(record)
    session.flush()
    return record


def _notice(session, source, title, body, days_ago=1, external_id="notice:1", raw=None):
    record = Announcement(
        source_id=source.id,
        external_id=external_id,
        title=title,
        body=body,
        category=NOTICE_CATEGORY,
        raw=raw or {},
        published_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=days_ago),
    )
    session.add(record)
    session.flush()
    return record


# --- JD documents -------------------------------------------------------


def test_a_jd_pdf_is_recognised_by_its_name():
    assert is_likely_jd("Cohesity_JD_2027.pdf") is True


def test_a_policy_guide_is_not_treated_as_a_jd():
    assert is_likely_jd("BMSCE_Placement_Policy_Student_Guide.pdf") is False


def test_spreadsheets_are_never_read():
    assert is_likely_jd("Job role list.xlsx") is False


def test_word_documents_are_read():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "<w:p><w:t>Required: Python</w:t></w:p>")
    assert "Required: Python" in document_text("JD.docx", buffer.getvalue())


def test_job_detail_documents_become_attachments():
    detail = {"jobProfile": {"documents": [{"name": "JD.pdf", "identifier": "doc-1"}]}}
    item = {"jobProfileIdentifier": "job-1", "jobProfileTitle": "Intern"}
    assert map_job_profile(item, detail).attachments == [
        {"name": "JD.pdf", "identifier": "doc-1"}
    ]


def test_documents_are_marked_checked_only_when_details_were_read():
    item = {"jobProfileIdentifier": "job-1", "jobProfileTitle": "Intern"}
    assert "documents_checked" not in map_job_profile(item, None).raw


# --- notices ------------------------------------------------------------


def test_full_notice_keeps_its_complete_text():
    item = {
        "identifier": "n-1",
        "title": "Nvidia Test Instructions",
        "content": "<p>Line one</p><p>Line two</p>",
    }
    assert map_notice(item, "https://app.joinsuperset.com").body == "Line one\nLine two"


def test_full_notice_links_to_its_page():
    item = {"identifier": "n-1", "title": "T", "content": ""}
    assert map_notice(item, "https://app.joinsuperset.com").url.endswith(
        "/students/notices/n-1"
    )


def test_company_key_skips_generic_words():
    assert notices.company_key("Cohesity India Pvt Ltd") == "cohesity"


def test_notice_mentioning_a_company_is_matched():
    assert (
        notices.mentioned_company("NVIDIA PROCESS update", ["NVIDIA Graphics Pvt Ltd"])
        == "NVIDIA Graphics Pvt Ltd"
    )


def test_a_name_inside_another_word_is_not_a_match():
    assert notices.mentioned_company("Hello from Zensarians", ["Zensar"]) is None


def test_applied_companies_include_superset_applications(session, source):
    _posting(session, source, "Cohesity India Pvt Ltd")
    _posting(session, source, "Acme", status="YET_TO_APPLY", external_id="job-2")
    assert notices.applied_companies(session) == ["Cohesity India Pvt Ltd"]


def test_applied_companies_include_tracked_applications(session, source):
    record = _posting(session, source, "Acme", status="YET_TO_APPLY")
    session.add(Application(opportunity_id=record.id, status="interview"))
    session.flush()
    assert notices.applied_companies(session) == ["Acme"]


def test_feed_about_applications_only_shows_matching_notices(session, source):
    _posting(session, source, "Cohesity India Pvt Ltd")
    _notice(session, source, "Cohesity Online Assessment", NOTICE_TEXT)
    _notice(session, source, "Workshop on GenAI", "Open to all", external_id="notice:2")
    feed = notices.notice_feed(session, only_applied=True)
    assert [item.record.title for item in feed] == ["Cohesity Online Assessment"]


def test_upcoming_lists_future_schedule_events(session, source):
    future = (dt.date.today() + dt.timedelta(days=5)).isoformat()
    _notice(
        session,
        source,
        "Test",
        "x",
        raw={"schedule": [{"what": "Online test", "date": future}]},
    )
    assert [event.what for event in notices.upcoming_events(session)] == ["Online test"]


def test_upcoming_skips_past_events(session, source):
    _notice(
        session,
        source,
        "Old",
        "x",
        raw={"schedule": [{"what": "Old test", "date": "2000-01-01"}]},
    )
    assert notices.upcoming_events(session) == []


# --- schedule extraction -----------------------------------------------


def test_a_date_written_with_a_month_name_is_found():
    assert is_date_in_text(dt.date(2026, 11, 24), NOTICE_TEXT) is True


def test_a_date_not_in_the_notice_is_rejected():
    assert is_date_in_text(dt.date(2026, 11, 25), NOTICE_TEXT) is False


def test_numeric_dates_are_found():
    assert is_date_in_text(dt.date(2026, 11, 24), "Test on 24/11/2026") is True


def test_extracted_events_keep_grounded_venue_and_time():
    answer = {
        "events": [
            {
                "what": "Online test",
                "date": "2026-11-24",
                "time": "10:00 AM",
                "venue": "CSE Seminar Hall",
            }
        ]
    }
    event = extract_schedule(
        NOTICE_TEXT,
        ScheduleLlm(answer),
        year_hint=2026,
        companies=["Cohesity"],
        default_company="Cohesity",
    )[0]
    assert (event["time"], event["venue"]) == ("10:00 AM", "CSE Seminar Hall")


def test_an_invented_date_is_dropped():
    answer = {"events": [{"what": "Interview", "date": "2026-12-03"}]}
    assert (
        extract_schedule(
            NOTICE_TEXT,
            ScheduleLlm(answer),
            year_hint=2026,
            companies=["Cohesity"],
            default_company="Cohesity",
        )
        == []
    )


def test_an_invented_venue_is_cleared():
    answer = {
        "events": [
            {"what": "Online test", "date": "2026-11-24", "venue": "Main Auditorium"}
        ]
    }
    assert (
        extract_schedule(
            NOTICE_TEXT,
            ScheduleLlm(answer),
            year_hint=2026,
            companies=["Cohesity"],
            default_company="Cohesity",
        )[0]["venue"]
        is None
    )


def test_no_answer_means_try_again_later():
    assert (
        extract_schedule(
            NOTICE_TEXT,
            ScheduleLlm(None),
            year_hint=2026,
            companies=["Cohesity"],
            default_company="Cohesity",
        )
        is None
    )


# --- the poll's schedule step ------------------------------------------


def test_poll_reads_schedules_only_for_applied_companies(session, source, monkeypatch):
    from app.jobs import poll
    from app.jobs.progress import ProgressReporter

    _posting(session, source, "Cohesity India Pvt Ltd")
    applied = _notice(session, source, "Cohesity Online Assessment", NOTICE_TEXT)
    other = _notice(session, source, "Workshop", "Open to all", external_id="notice:2")
    answer = {"events": [{"what": "Online test", "date": "2026-11-24"}]}
    monkeypatch.setattr(
        "app.services.llm.LlmClient.configured", property(lambda self: True)
    )
    monkeypatch.setattr(
        "app.services.llm.LlmClient.complete_json", lambda self, s, u, timeout=0: answer
    )
    poll._read_schedules(session, make_settings(), ProgressReporter(None))
    assert (
        applied.raw["schedule"][0]["what"] == "Online test"
        and "schedule_checked" not in other.raw
    )


def test_poll_retries_a_notice_when_the_model_gives_no_answer(
    session, source, monkeypatch
):
    from app.jobs import poll
    from app.jobs.progress import ProgressReporter

    _posting(session, source, "Cohesity India Pvt Ltd")
    notice = _notice(session, source, "Cohesity Online Assessment", NOTICE_TEXT)
    monkeypatch.setattr(
        "app.services.llm.LlmClient.configured", property(lambda self: True)
    )
    monkeypatch.setattr(
        "app.services.llm.LlmClient.complete_json", lambda self, s, u, timeout=0: None
    )
    poll._read_schedules(session, make_settings(), ProgressReporter(None))
    assert "schedule_checked" not in notice.raw


def test_poll_reads_at_most_the_configured_number_of_notices(
    session, source, monkeypatch
):
    from app.jobs import poll
    from app.jobs.progress import ProgressReporter

    _posting(session, source, "Cohesity India Pvt Ltd")
    for index in range(4):
        _notice(
            session,
            source,
            f"Cohesity round {index}",
            NOTICE_TEXT,
            external_id=f"notice:{index}",
        )
    monkeypatch.setattr(
        "app.services.llm.LlmClient.configured", property(lambda self: True)
    )
    monkeypatch.setattr(
        "app.services.llm.LlmClient.complete_json",
        lambda self, s, u, timeout=0: {"events": []},
    )
    settings = make_settings(schedule_max_per_poll=2)
    assert poll._read_schedules(session, settings, ProgressReporter(None)) == 2


def test_old_notices_are_not_sent_to_the_model(session, source, monkeypatch):
    from app.jobs import poll
    from app.jobs.progress import ProgressReporter

    _posting(session, source, "Cohesity India Pvt Ltd")
    _notice(session, source, "Cohesity old round", NOTICE_TEXT, days_ago=60)
    monkeypatch.setattr(
        "app.services.llm.LlmClient.configured", property(lambda self: True)
    )
    monkeypatch.setattr(
        "app.services.llm.LlmClient.complete_json", lambda *a, **k: pytest.fail("no AI")
    )
    assert poll._read_schedules(session, make_settings(), ProgressReporter(None)) == 0


WEEKLY = (
    "Weekly Tentative Schedule 2027 Batch\n"
    "Date | Company | Process\n"
    "02-11-2026 | BCE TECH | PPT/Test\n"
    "Nvidia | Test\n"
    "03-11-2026 | Enerparc Energy | Full Process\n"
    "IBM | Test\n"
    "04-11-2026 | Thoughtworks | Interview"
)


def test_weekly_schedule_keeps_only_applied_companies():
    events = parse_schedule_table(WEEKLY, ["Nvidia", "IBM"])
    assert [(event["company"], event["date"], event["what"]) for event in events] == [
        ("Nvidia", "2026-11-02", "Test"),
        ("IBM", "2026-11-03", "Test"),
    ]


def test_a_row_without_a_date_continues_the_date_above():
    events = parse_schedule_table(WEEKLY, ["Nvidia"])
    assert events[0]["date"] == "2026-11-02"


def test_weekly_schedule_is_recognised_as_a_timetable():
    assert (
        notices.is_schedule_table("Weekly Tentative Schedule 2027 Batch", WEEKLY)
        is True
    )


def test_an_ordinary_word_company_needs_both_words():
    assert (
        notices.mentioned_company("A bright future awaits", ["Future Standard"]) is None
    )


def test_ai_events_for_other_companies_are_dropped():
    text = "Accenture test on 24th Nov. IBM interview on 25th Nov."
    answer = {
        "events": [
            {"what": "Test", "company": "Accenture", "date": "2026-11-24"},
            {"what": "Interview", "company": "IBM", "date": "2026-11-25"},
        ]
    }
    events = extract_schedule(
        text,
        ScheduleLlm(answer),
        year_hint=2026,
        companies=["IBM"],
        default_company="IBM",
    )
    assert [event["company"] for event in events] == ["IBM"]


def test_events_on_two_days_are_kept_as_two_events():
    text = "Interviews on 10th & 11th Nov."
    answer = {
        "events": [
            {"what": "Interview", "company": None, "date": "2026-11-10"},
            {"what": "Interview", "company": None, "date": "2026-11-11"},
        ]
    }
    events = extract_schedule(
        text,
        ScheduleLlm(answer),
        year_hint=2026,
        companies=["IBM"],
        default_company="IBM",
    )
    assert [event["date"] for event in events] == ["2026-11-10", "2026-11-11"]


def test_timetable_notices_are_read_without_the_ai(session, source, monkeypatch):
    from app.jobs import poll
    from app.jobs.progress import ProgressReporter

    _posting(session, source, "Nvidia")
    notice = _notice(session, source, "Weekly Tentative Schedule 2027 Batch", WEEKLY)
    monkeypatch.setattr(
        "app.services.llm.LlmClient.complete_json", lambda *a, **k: pytest.fail("no AI")
    )
    poll._read_schedules(session, make_settings(), ProgressReporter(None))
    assert notice.raw["schedule"][0]["company"] == "Nvidia"


def test_application_openings_are_skipped_without_the_ai(session, source, monkeypatch):
    from app.jobs import poll
    from app.jobs.progress import ProgressReporter

    _posting(session, source, "Nvidia")
    notice = _notice(
        session,
        source,
        "Open for applications - Nvidia's Job Profile",
        "Apply by 2 Nov",
    )
    monkeypatch.setattr(
        "app.services.llm.LlmClient.complete_json", lambda *a, **k: pytest.fail("no AI")
    )
    poll._read_schedules(session, make_settings(), ProgressReporter(None))
    assert notice.raw["schedule_checked"] is True


def test_process_notices_are_read_before_others(session, source):
    _posting(session, source, "IBM")
    _notice(
        session, source, "IBM hiring webinar", "IBM", external_id="notice:a", days_ago=1
    )
    _notice(
        session, source, "IBM-PROCESS", "IBM test", external_id="notice:b", days_ago=3
    )
    pending = notices.notices_needing_schedule(session, max_age_days=21, limit=1)
    assert pending[0][0].title == "IBM-PROCESS"


def test_a_specific_notice_hides_the_tentative_entry_for_the_same_day(session, source):
    day = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    _notice(
        session,
        source,
        "Weekly schedule",
        "x",
        external_id="notice:w",
        raw={
            "schedule": [
                {"what": "Test", "company": "Nvidia", "date": day, "tentative": True}
            ]
        },
    )
    _notice(
        session,
        source,
        "NVIDIA PROCESS",
        "x",
        external_id="notice:n",
        raw={
            "company": "Nvidia",
            "schedule": [{"what": "Test", "date": day, "time": "5:30 PM"}],
        },
    )
    assert [event.time for event in notices.upcoming_events(session)] == ["5:30 PM"]


def test_tentative_entries_stay_when_nothing_confirms_them(session, source):
    day = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    _notice(
        session,
        source,
        "Weekly schedule",
        "x",
        raw={
            "schedule": [
                {
                    "what": "Interview",
                    "company": "Thoughtworks",
                    "date": day,
                    "tentative": True,
                }
            ]
        },
    )
    assert notices.upcoming_events(session)[0].is_tentative is True


def test_a_one_letter_typo_in_a_timetable_still_matches():
    events = parse_schedule_table(WEEKLY + "\nDayfoce | PPT/Test", ["Dayforce"])
    assert [(event["company"], event["date"]) for event in events] == [
        ("Dayforce", "2026-11-04")
    ]


def test_typo_tolerance_does_not_confuse_short_names():
    assert notices.company_in_cell("IBX", ["IBM"]) is None


def test_a_confirmed_event_mentions_the_weekly_schedule(session, source):
    day = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    _notice(
        session,
        source,
        "Weekly schedule",
        "x",
        external_id="notice:w",
        raw={
            "schedule": [
                {"what": "PPT/Test", "company": "IBM", "date": day, "tentative": True}
            ]
        },
    )
    _notice(
        session,
        source,
        "IBM-PROCESS",
        "x",
        external_id="notice:i",
        raw={
            "company": "IBM",
            "schedule": [{"what": "Coding assessment", "date": day, "time": "6:00 PM"}],
        },
    )
    assert (
        notices.upcoming_events(session)[0].schedule_note
        == "Also in the weekly schedule: PPT/Test"
    )


def test_notice_tables_render_as_html_tables():
    from app.web.notice_html import notice_to_html

    rendered = str(notice_to_html("Note\nDate | Company\n02-11-2026 | Nvidia\nIBM"))
    assert "<th>Company</th>" in rendered and "<td>Nvidia</td>" in rendered


def test_notice_text_is_escaped():
    from app.web.notice_html import notice_to_html

    assert "<script>" not in str(notice_to_html("<script>alert(1)</script>"))


def test_a_timetable_notice_lists_every_applied_company_in_it(session, source):
    record = _notice(
        session,
        source,
        "Weekly schedule",
        "x",
        raw={
            "schedule": [
                {"what": "Test", "company": "Nvidia", "date": "2026-11-02"},
                {"what": "Test", "company": "IBM", "date": "2026-11-03"},
            ]
        },
    )
    assert notices.NoticeView(record, "Nvidia").companies == ["Nvidia", "IBM"]


def test_a_company_inside_a_web_link_is_not_a_mention():
    text = "Books: https://www.amazon.in/Station-Master-Guide/dp/B0HN"
    assert notices.mentioned_company(text, ["Amazon"]) is None


def test_schedule_venue_drops_leading_bullets():
    from app.services.schedule import _grounded_event

    text = "Date: 02-11-2026\nVenue:* ( Test ) PJB Block"
    event = _grounded_event({"date": "2026-11-02", "venue": "* ( Test ) PJB Block"}, text)
    assert event["venue"] == "( Test ) PJB Block"


def test_long_schedule_notes_end_on_a_whole_word():
    from app.services.schedule import _grounded_event

    event = _grounded_event({"date": "2026-11-02", "notes": "bring laptops " * 40}, "02-11-2026")
    assert event["notes"].endswith("laptops…")
