"""End-to-end tests of the agent graph with faked network and model."""

from __future__ import annotations

from agent_helpers import (
    LONG_PAST,
    FakeLlm,
    FakeTools,
    make_deps,
    make_search_result,
    make_settings,
    make_state,
)

from app.agent.graph import run_graph
from app.agent.state import Route

WIKI = "https://en.wikipedia.org/wiki/Zensar"
FACT = "Zensar Technologies is an Indian IT services company founded in 1963"
PARSED_JD = {
    "skills": ["Python", "SQL"],
    "rounds": ["Online Assessment"],
    "pay": {"raw": "12 LPA"},
    "unknown_fields": ["bond"],
}


def _tools(**kwargs) -> FakeTools:
    results = [make_search_result(FACT, WIKI)]
    return FakeTools(make_settings(), default_results=results, **kwargs)


def test_dropped_item_makes_no_tool_calls():
    tools = _tools()
    run_graph(make_state(deadline=LONG_PAST), make_deps(tools))
    assert tools.calls == []


def test_dropped_item_ends_on_the_drop_route():
    state = run_graph(make_state(deadline=LONG_PAST), make_deps(_tools()))
    assert state.route is Route.DROP


def test_short_path_scores_fit_without_searching_the_web():
    tools = _tools()
    state = run_graph(make_state(eligible=False, parsed_jd=PARSED_JD), make_deps(tools))
    assert state.fit is not None and "web_search" not in tools.tool_names()


def test_full_path_produces_a_verified_cited_claim():
    state = run_graph(make_state(parsed_jd=PARSED_JD), make_deps(_tools()))
    assert [claim.source_url for claim in state.claims] == [WIKI]


def test_full_path_produces_fit_and_resume_advice():
    state = run_graph(make_state(parsed_jd=PARSED_JD), make_deps(_tools()))
    assert state.fit is not None and state.resume_advice is not None


def test_full_path_records_what_the_jd_does_not_state():
    state = run_graph(make_state(parsed_jd=PARSED_JD), make_deps(_tools()))
    assert "JD does not state: bond" in state.unknowns


def test_trace_lists_the_nodes_that_ran():
    state = run_graph(make_state(parsed_jd=PARSED_JD), make_deps(_tools()))
    assert [entry.split(":")[0] for entry in state.trace] == [
        "route",
        "plan",
        "specialists",
        "critic",
        "finalise",
        "writer",
        "approval",
    ]


def test_clean_claims_cause_no_retry():
    state = run_graph(make_state(parsed_jd=PARSED_JD), make_deps(_tools()))
    assert state.retry_count == 0


def test_invented_model_claim_triggers_exactly_one_retry():
    llm = FakeLlm(
        claims=[{"evidence_id": 0, "topic": "x", "text": "Zensar has 99999 employees"}]
    )
    state = run_graph(make_state(parsed_jd=PARSED_JD), make_deps(_tools(), llm))
    assert state.retry_count == 1


def test_retry_replaces_the_invented_claim_with_a_quoted_one():
    llm = FakeLlm(
        claims=[{"evidence_id": 0, "topic": "x", "text": "Zensar has 99999 employees"}]
    )
    state = run_graph(make_state(parsed_jd=PARSED_JD), make_deps(_tools(), llm))
    assert [claim.text for claim in state.claims] == [FACT]


def test_no_evidence_means_no_claims_and_no_retry():
    tools = FakeTools(make_settings())
    state = run_graph(make_state(parsed_jd=PARSED_JD), make_deps(tools))
    assert state.claims == [] and state.retry_count == 0
