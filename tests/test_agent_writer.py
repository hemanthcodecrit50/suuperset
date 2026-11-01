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


def test_writer_rejects_a_report_with_an_invented_number():
    llm = WriterLlm("### Verdict\nLean apply: the package is 45 LPA.")
    state = make_state()

    write_report(state, make_deps(llm=llm))

    assert state.report_markdown == render_template(state)


def test_writer_keeps_numbers_that_come_from_the_jd():
    llm = WriterLlm("### Key facts\n- Bond of 24 months")
    state = make_state(parsed_jd={"bond": {"applicable": True, "months": 24}})

    write_report(state, make_deps(llm=llm))

    assert state.report_markdown == "### Key facts\n- Bond of 24 months\n"


def test_writer_sends_the_verified_draft_to_the_model():
    llm = WriterLlm("### Verdict\nThink carefully.")
    state = make_state()

    write_report(state, make_deps(llm=llm))

    assert llm.calls[0][1].startswith("DRAFT:")


def test_full_graph_sets_report_markdown():
    state = make_state(parsed_jd={"skills": ["Python"]})
    state = run_graph(state, make_deps())

    assert state.report_markdown


def test_short_graph_also_sets_report_markdown():
    state = make_state(eligible=False)

    state = run_graph(state, make_deps())

    assert state.report_markdown


def test_empty_profile_brief_does_not_claim_a_fit_score():
    from agent_helpers import make_memory

    from app.agent.state import Route

    state = make_state(memory=make_memory(is_profile_set=False))
    state.route = Route.FULL
    state.fit = {"score": 10.0, "eligibility": "unknown", "reasons": [], "unknown": []}
    assert "not scored yet" in render_template(state)


def test_empty_profile_brief_reports_a_jd_without_requirements():
    from agent_helpers import make_memory

    from app.agent.state import Route

    state = make_state(memory=make_memory(is_profile_set=False))
    state.route = Route.FULL
    state.fit = {"score": 10.0, "eligibility": "eligible", "reasons": [], "unknown": []}
    assert "publishes no hard requirements" in render_template(state)
