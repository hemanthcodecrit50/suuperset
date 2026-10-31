"""Tests for the researcher node."""

from __future__ import annotations

from agent_helpers import FakeLlm, FakeTools, make_deps, make_search_result, make_state

from app.agent.planner import BOND, COMPANY_OVERVIEW, Task
from app.agent.researcher import research

WIKI = "https://en.wikipedia.org/wiki/Zensar"
BLOG = "https://forum.test/zensar-bond"


def _deps(results, llm=None):
    settings = make_deps().settings
    return make_deps(FakeTools(settings, default_results=results), llm)


def _state(tasks):
    state = make_state()
    state.tasks = tasks
    return state


def test_every_planned_task_is_searched():
    tasks = [Task(COMPANY_OVERVIEW, "Zensar", "r"), Task(BOND, "Zensar bond", "r")]
    deps = _deps([make_search_result("Zensar is an IT company", WIKI)])
    research(_state(tasks), deps)
    assert sorted(arguments["query"] for _, arguments in deps.call_tool.calls) == [
        "Zensar",
        "Zensar bond",
    ]


def test_evidence_is_tagged_with_the_topic_that_found_it():
    deps = _deps([make_search_result("Zensar is an IT company", WIKI)])
    state = research(_state([Task(COMPANY_OVERVIEW, "Zensar", "r")]), deps)
    assert state.evidence[0].topic == COMPANY_OVERVIEW


def test_bond_findings_from_non_official_sources_are_labelled_anecdotal():
    deps = _deps([make_search_result("Employees mention a 2 year bond", BLOG, "web")])
    state = research(_state([Task(BOND, "Zensar bond", "r")]), deps)
    assert state.claims[0].confidence == "low"


def test_duplicate_urls_are_merged():
    deps = _deps([make_search_result("Zensar is an IT company", WIKI)])
    tasks = [
        Task(COMPANY_OVERVIEW, "Zensar", "r"),
        Task("recent_news", "Zensar news", "r"),
    ]
    state = research(_state(tasks), deps)
    assert len(state.evidence) == 1


def test_failed_search_yields_no_evidence_instead_of_an_error():
    tools = FakeTools(make_deps().settings)
    tools.__class__ = type(
        "Broken",
        (FakeTools,),
        {
            "__call__": lambda self, name, arguments: {
                "ok": False,
                "tool": name,
                "error": "down",
            }
        },
    )
    state = research(_state([Task(COMPANY_OVERVIEW, "Zensar", "r")]), make_deps(tools))
    assert state.evidence == [] and state.claims == []


def test_without_a_model_each_evidence_item_becomes_one_claim():
    deps = _deps([make_search_result("Zensar is an IT company", WIKI)])
    state = research(_state([Task(COMPANY_OVERVIEW, "Zensar", "r")]), deps)
    assert [claim.text for claim in state.claims] == ["Zensar is an IT company"]


def test_model_claims_take_their_url_from_the_evidence_not_the_model():
    llm = FakeLlm(
        claims=[{"evidence_id": 0, "topic": "x", "text": "Zensar is an IT company"}]
    )
    deps = _deps([make_search_result("Zensar is an IT company", WIKI)], llm)
    state = research(_state([Task(COMPANY_OVERVIEW, "Zensar", "r")]), deps)
    assert state.claims[0].source_url == WIKI


def test_model_claim_pointing_at_a_missing_evidence_id_is_discarded():
    llm = FakeLlm(claims=[{"evidence_id": 7, "topic": "x", "text": "Made up"}])
    deps = _deps([make_search_result("Zensar is an IT company", WIKI)], llm)
    state = research(_state([Task(COMPANY_OVERVIEW, "Zensar", "r")]), deps)
    assert [claim.text for claim in state.claims] == ["Zensar is an IT company"]


def test_retry_pass_ignores_the_model_and_quotes_the_evidence():
    llm = FakeLlm(
        claims=[{"evidence_id": 0, "topic": "x", "text": "Zensar has 99999 staff"}]
    )
    deps = _deps([make_search_result("Zensar is an IT company", WIKI)], llm)
    state = _state([Task(COMPANY_OVERVIEW, "Zensar", "r")])
    state.retry_count = 1
    state.retry_topics = [COMPANY_OVERVIEW]
    state = research(state, deps)
    assert [claim.text for claim in state.claims] == ["Zensar is an IT company"]


def test_retry_pass_searches_again_for_each_failed_topic():
    deps = _deps([])
    state = _state([Task(COMPANY_OVERVIEW, "Zensar", "r")])
    state.retry_count = 1
    state.retry_topics = [BOND]
    research(state, deps)
    assert [arguments["query"] for _, arguments in deps.call_tool.calls] == [
        "Zensar bond"
    ]


def test_results_about_other_entities_are_discarded():
    deps = _deps(
        [
            make_search_result(
                "CMR Institute is an engineering college", "https://x.test/cmr"
            )
        ]
    )
    state = research(_state([Task(COMPANY_OVERVIEW, "Zensar", "r")]), deps)
    assert state.evidence == []
