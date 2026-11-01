"""Tests for the Dev mode event log."""

from __future__ import annotations

from app.services.events import EventLog, read_events


def test_recorded_event_can_be_read_back(tmp_path):
    path = tmp_path / "events.jsonl"
    EventLog(path).record("agent", "route", "FULL · New posting", run_id="run-1")
    assert read_events(path)[0].summary == "FULL · New posting"


def test_events_are_returned_newest_first(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.record("agent", "route", "first")
    log.record("agent", "plan", "second")
    assert [event.name for event in read_events(path)] == ["plan", "route"]


def test_events_can_be_filtered_by_run(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.record("agent", "route", "a", run_id="run-1")
    log.record("agent", "route", "b", run_id="run-2")
    assert [event.summary for event in read_events(path, run_id="run-2")] == ["b"]


def test_events_can_be_filtered_by_source(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.record("tool", "web_search", "a")
    log.record("ai", "request", "b")
    assert [event.source for event in read_events(path, source="ai")] == ["ai"]


def test_log_without_a_file_records_nothing(tmp_path):
    EventLog(None).record("agent", "route", "a")
    assert list(tmp_path.iterdir()) == []


def test_missing_log_reads_as_empty(tmp_path):
    assert read_events(tmp_path / "none.jsonl") == []


def test_corrupt_lines_are_skipped(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("not json\n")
    EventLog(path).record("agent", "route", "ok")
    assert [event.summary for event in read_events(path)] == ["ok"]
