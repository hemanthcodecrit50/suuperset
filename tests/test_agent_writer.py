"""Tests for the Week 3 report writer node."""

from __future__ import annotations

from agent_helpers import make_deps, make_state

from app.agent.brief import render_template
from app.agent.graph import run_graph
from app.agent.state import Claim
from app.agent.writer import WRITER_SYSTEM, write_report


class WriterLlm:
    configured = True

    def __init__(self, report: str | None) -> None:
        self.report = report
        self.calls: list[tuple[str, str]] = []

    def complete_text(
        self, system: str, user: str, timeout: float = 0, max_chars: int = 0
    ) -> str | None:
        self.calls.append((system, user))
        return self.report


def test_writer_falls_back_without_an_llm():
    state = make_state()
    deps = make_deps(llm=None)

    write_report(state, deps)

    assert state.report_markdown == render_template(state)


def test_writer_uses_llm_report_and_adds_newline():
    llm = WriterLlm("## Critical Information Report")
    state = make_state()
    deps = make_deps(llm=llm)

    write_report(state, deps)

    assert state.report_markdown == "## Critical Information Report\n"
    assert llm.calls[0][0] == WRITER_SYSTEM


def test_writer_empty_llm_response_uses_fallback():
    llm = WriterLlm("")
    state = make_state()
    deps = make_deps(llm=llm)

    write_report(state, deps)

    assert state.report_markdown == render_template(state)


def test_writer_rejects_unapproved_source_urls():
    llm = WriterLlm("Facts: https://untrusted.example/report")
    state = make_state()
    state.claims = [
        Claim(
            text="Zensar is an IT services company",
            source_url="https://trusted.example/report",
            topic="company_overview",
            is_verified=True,
        )
    ]
    deps = make_deps(llm=llm)

    write_report(state, deps)

    assert state.report_markdown == render_template(state)


def test_full_graph_sets_report_markdown():
    state = make_state(parsed_jd={"skills": ["Python"]})
    state = run_graph(state, make_deps())

    assert state.report_markdown


def test_short_graph_also_sets_report_markdown():
    state = make_state(eligible=False)

    state = run_graph(state, make_deps())

    assert state.report_markdown
