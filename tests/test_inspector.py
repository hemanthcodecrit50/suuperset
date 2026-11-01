"""Tests for the Dev mode views built from events."""

from __future__ import annotations

from app.services.events import Event
from app.web.inspector import build_agent_run, build_collector_run, group_events


def _event(
    source,
    name,
    summary="",
    run_id="agent-1-1",
    duration_ms=10,
    at="2026-11-01T10:00:00+00:00",
):
    return Event(
        at=at,
        source=source,
        name=name,
        summary=summary,
        run_id=run_id,
        duration_ms=duration_ms,
    )


def test_finished_nodes_are_marked_done():
    run = build_agent_run(
        "agent-1-1", [_event("agent", "route", "FULL")], None, is_running=False
    )
    assert run.nodes["route"].status == "done"


def test_nodes_that_never_ran_are_skipped_after_a_run():
    run = build_agent_run(
        "agent-1-1", [_event("agent", "route", "SHORT")], None, is_running=False
    )
    assert run.nodes["plan"].status == "skipped"


def test_next_node_is_running_during_a_run():
    run = build_agent_run(
        "agent-1-1", [_event("agent", "route")], None, is_running=True
    )
    assert run.nodes["plan"].status == "running"


def test_specialists_run_together():
    events = [_event("agent", "route"), _event("agent", "plan")]
    run = build_agent_run("agent-1-1", events, None, is_running=True)
    assert (
        run.nodes["matcher"].status == "running"
        and run.nodes["researcher"].status == "running"
    )


def test_retry_marks_the_researcher_as_retried():
    events = [_event("agent", "researcher"), _event("agent", "research_retry")]
    run = build_agent_run("agent-1-1", events, None, is_running=False)
    assert run.was_retried is True


def test_tool_calls_and_ai_requests_are_separated():
    events = [_event("tool", "web_search"), _event("ai", "request")]
    run = build_agent_run("agent-1-1", events, None, is_running=False)
    assert len(run.tool_calls) == 1 and len(run.ai_requests) == 1


def test_critic_rows_come_from_the_saved_brief():
    brief = {
        "claims": [{"text": "a", "source_url": "https://x.org/1", "check_note": "ok"}],
        "dropped_claims": [],
    }
    run = build_agent_run("agent-1-1", [], brief, is_running=False)
    assert run.critic_rows[0].domain == "x.org"


def test_collector_stage_before_the_next_one_is_done():
    events = [
        _event(
            "collector", "stage", "Opening Superset with your saved session", "poll-1"
        ),
        _event(
            "collector",
            "stage",
            "Reading announcements",
            "poll-1",
            at="2026-11-01T10:00:02+00:00",
        ),
    ]
    run = build_collector_run("poll-1", events, is_running=True)
    assert [stage.status for stage in run.stages[:3]] == ["done", "running", "waiting"]


def test_events_of_one_run_are_grouped():
    events = [
        _event("agent", "writer"),
        _event("agent", "route"),
        _event("collector", "stage", run_id=None),
    ]
    assert [len(group.events) for group in group_events(events)] == [2, 1]
