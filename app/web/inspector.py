"""Dev mode views built from recorded events: one agent run, and the latest poll."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from app.jobs.progress import STAGES
from app.services.events import Event

NODE_LABELS = {
    "route": "Router",
    "plan": "Planner",
    "researcher": "Researcher",
    "matcher": "Matcher",
    "resume_advisor": "Resume advisor",
    "critic": "Critic",
    "writer": "Writer",
    "approval": "Approval gate",
}
# Order a run moves through, used to tell which node is running right now
NODE_ORDER = (
    "route",
    "plan",
    "researcher",
    "matcher",
    "resume_advisor",
    "critic",
    "writer",
    "approval",
)
PARALLEL_NODES = ("researcher", "matcher", "resume_advisor")
COLLECTOR_STAGES = ("connect", "announcements", "jobs", "save", "done")
STAGE_TITLES = {
    "connect": "Session check",
    "announcements": "Announcements",
    "jobs": "Job profiles",
    "save": "Change detection",
    "done": "Finished",
}


@dataclass
class NodeView:
    name: str
    label: str
    status: str  # done | running | waiting | skipped | retried
    seconds: float | None = None
    summary: str = ""


@dataclass
class CriticRow:
    text: str
    domain: str
    note: str
    is_supported: bool


@dataclass
class AgentRunView:
    run_id: str
    is_running: bool
    seconds: float
    nodes: dict[str, NodeView]
    tool_calls: list[Event]
    ai_requests: list[Event]
    discards: list[Event]
    critic_rows: list[CriticRow] = field(default_factory=list)
    planned: list[dict[str, Any]] = field(default_factory=list)

    @property
    def was_retried(self) -> bool:
        return self.nodes["researcher"].status == "retried"


@dataclass
class StageView:
    key: str
    title: str
    status: str  # done | running | waiting | failed
    seconds: float | None
    detail: str


@dataclass
class CollectorRunView:
    run_id: str
    stages: list[StageView]
    changes: list[Event]
    guard: Event | None


def build_agent_run(
    run_id: str, events: list[Event], brief: dict[str, Any] | None, is_running: bool
) -> AgentRunView:
    """Assemble one agent run; ``events`` are oldest first."""
    by_name = {event.name: event for event in events if event.source == "agent"}
    return AgentRunView(
        run_id=run_id,
        is_running=is_running,
        seconds=_run_seconds(events, by_name, is_running),
        nodes=_nodes(by_name, is_running),
        tool_calls=[event for event in events if event.source == "tool"],
        ai_requests=[event for event in events if event.source == "ai"],
        discards=[event for event in events if event.name == "discard"],
        critic_rows=_critic_rows(brief) if brief else [],
        planned=(brief or {}).get("tasks") or [],
    )


def build_collector_run(
    run_id: str, events: list[Event], is_running: bool
) -> CollectorRunView:
    """Assemble the latest poll; ``events`` are oldest first."""
    stage_events = [event for event in events if event.name == "stage"]
    return CollectorRunView(
        run_id=run_id,
        stages=_stages(stage_events, is_running),
        changes=[event for event in events if event.name in ("new", "changed")],
        guard=next((event for event in events if event.name == "guards"), None),
    )


def _nodes(by_name: dict[str, Event], is_running: bool) -> dict[str, NodeView]:
    running = _running_nodes(by_name) if is_running else set()
    nodes: dict[str, NodeView] = {}
    for name in NODE_ORDER:
        event = by_name.get(name)
        if event is not None:
            nodes[name] = NodeView(
                name, NODE_LABELS[name], "done", _seconds(event), event.summary
            )
        elif name in running:
            nodes[name] = NodeView(name, NODE_LABELS[name], "running")
        else:
            nodes[name] = NodeView(
                name, NODE_LABELS[name], "waiting" if is_running else "skipped"
            )
    if "research_retry" in by_name:
        nodes["researcher"].status = "retried"
        nodes["researcher"].summary = by_name["research_retry"].summary
    if "critic_retry" in by_name:
        nodes["critic"].summary = by_name["critic_retry"].summary
    return nodes


def _running_nodes(by_name: dict[str, Event]) -> set[str]:
    missing = [name for name in NODE_ORDER if name not in by_name]
    if not missing:
        return set()
    # The three specialists run side by side, so they are running together
    if missing[0] in PARALLEL_NODES:
        return {name for name in PARALLEL_NODES if name in missing}
    return {missing[0]}


def _critic_rows(brief: dict[str, Any]) -> list[CriticRow]:
    kept = [_critic_row(claim, True) for claim in brief.get("claims") or []]
    dropped = [_critic_row(claim, False) for claim in brief.get("dropped_claims") or []]
    return kept + dropped


def _critic_row(claim: dict[str, Any], is_supported: bool) -> CriticRow:
    return CriticRow(
        text=claim.get("text", ""),
        domain=urlparse(claim.get("source_url", "")).netloc.removeprefix("www."),
        note=claim.get("check_note") or "",
        is_supported=is_supported,
    )


def _stages(stage_events: list[Event], is_running: bool) -> list[StageView]:
    started = {_stage_key(event): event for event in stage_events}
    failure = started.get("failed")
    views: list[StageView] = []
    for index, key in enumerate(COLLECTOR_STAGES):
        event = started.get(key)
        if event is None:
            views.append(StageView(key, STAGE_TITLES[key], "waiting", None, ""))
            continue
        next_stage = next(
            (
                started[later]
                for later in COLLECTOR_STAGES[index + 1 :]
                if later in started
            ),
            None,
        )
        views.append(
            StageView(
                key,
                STAGE_TITLES[key],
                _stage_status(key, next_stage, failure, is_running),
                _between(event, next_stage or failure),
                _detail(event),
            )
        )
    return views


def _stage_status(
    key: str, next_stage: Event | None, failure: Event | None, is_running: bool
) -> str:
    if next_stage is not None or key == "done":
        return "done"
    if failure is not None:
        return "failed"
    return "running" if is_running else "done"


def _stage_key(event: Event) -> str:
    for key, (label, _, _) in STAGES.items():
        if event.summary.startswith(label):
            return key
    return "unknown"


def _detail(event: Event) -> str:
    parts = event.summary.split(" · ", 1)
    return parts[1] if len(parts) > 1 else ""


def _run_seconds(
    events: list[Event], by_name: dict[str, Event], is_running: bool
) -> float:
    if "run_finished" in by_name:
        return _seconds(by_name["run_finished"]) or 0.0
    if not events:
        return 0.0
    end = dt.datetime.now(dt.UTC) if is_running else _time(events[-1])
    return round((end - _time(events[0])).total_seconds(), 1)


def _seconds(event: Event) -> float | None:
    return round(event.duration_ms / 1000, 2) if event.duration_ms is not None else None


def _between(start: Event, end: Event | None) -> float | None:
    if end is None:
        return None
    return round((_time(end) - _time(start)).total_seconds(), 1)


def _time(event: Event) -> dt.datetime:
    return dt.datetime.fromisoformat(event.at)


@dataclass
class EventGroup:
    run_id: str | None
    title: str
    events: list[Event]

    @property
    def kind(self) -> str:
        if self.run_id is None:
            return "single"
        return "agent" if self.run_id.startswith("agent-") else "collector"


def group_events(events: list[Event]) -> list[EventGroup]:
    """Bundle consecutive events of one run under a single header, newest first."""
    groups: list[EventGroup] = []
    for event in events:
        if groups and event.run_id is not None and groups[-1].run_id == event.run_id:
            groups[-1].events.append(event)
        else:
            groups.append(EventGroup(event.run_id, "", [event]))
    for group in groups:
        group.title = _group_title(group)
    return groups


def _group_title(group: EventGroup) -> str:
    if group.run_id is None:
        return ""
    if group.run_id.startswith("agent-"):
        started = next(
            (event for event in group.events if event.name == "run_started"), None
        )
        return f"Agent run · {started.summary}" if started else "Agent run"
    return "Collector poll"
