"""Tests for the poll progress file behind the web progress bar."""

from __future__ import annotations

from app.jobs.progress import ProgressReporter, read_progress


def test_progress_is_readable_after_an_update(tmp_path):
    path = tmp_path / "progress.json"
    ProgressReporter(path).update("announcements")
    assert read_progress(path).stage == "announcements"


def test_percent_moves_through_the_stage_slice(tmp_path):
    path = tmp_path / "progress.json"
    ProgressReporter(path).update("jobs", "50 of 100", done=50, total=100)
    assert read_progress(path).percent == 40


def test_finished_poll_reports_full_progress(tmp_path):
    path = tmp_path / "progress.json"
    ProgressReporter(path).update("done", "3 new")
    assert read_progress(path).percent == 100


def test_detail_text_is_kept(tmp_path):
    path = tmp_path / "progress.json"
    ProgressReporter(path).update("save", "Writing agent brief for SDE 1", 3, 10)
    assert read_progress(path).detail == "Writing agent brief for SDE 1"


def test_missing_file_reads_as_no_progress(tmp_path):
    assert read_progress(tmp_path / "none.json") is None


def test_reporter_without_a_file_does_nothing(tmp_path):
    ProgressReporter(None).update("connect")
    assert list(tmp_path.iterdir()) == []
