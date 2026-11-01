"""Tests for the planner node."""

from __future__ import annotations

from agent_helpers import make_evidence, make_memory, make_opportunity

from app.agent.planner import (
    BOND,
    COMPANY_OVERVIEW,
    INTERVIEW_PROCESS,
    MAX_TASKS,
    PAY,
    RECENT_NEWS,
    plan_tasks,
)

STATED_PAY_JD = {"pay": {"raw": "12 LPA"}, "rounds": ["Online Assessment"]}


def _topics(opportunity=None, parsed_jd=None, memory=None) -> list[str]:
    tasks = plan_tasks(
        opportunity or make_opportunity(),
        STATED_PAY_JD if parsed_jd is None else parsed_jd,
        memory or make_memory(),
    )
    return [task.topic for task in tasks]


def test_unfamiliar_company_gets_overview_and_news_lookups():
    topics = _topics()
    assert COMPANY_OVERVIEW in topics and RECENT_NEWS in topics


def test_cached_company_evidence_skips_the_overview_and_news():
    memory = make_memory(
        cached_evidence=[make_evidence("Zensar is an IT firm", "https://x.test")]
    )
    topics = _topics(memory=memory)
    assert COMPANY_OVERVIEW not in topics and RECENT_NEWS not in topics


def test_bond_clause_adds_a_bond_lookup():
    parsed_jd = {**STATED_PAY_JD, "bond": {"applicable": True, "raw": "12 months"}}
    assert BOND in _topics(parsed_jd=parsed_jd)


def test_no_bond_clause_means_no_bond_lookup():
    assert BOND not in _topics()


def test_missing_pay_adds_a_pay_lookup():
    assert PAY in _topics(
        make_opportunity(compensation=None), parsed_jd={"rounds": ["OA"]}
    )


def test_stated_pay_means_no_pay_lookup():
    assert PAY not in _topics()


def test_missing_rounds_add_an_interview_lookup():
    assert INTERVIEW_PROCESS in _topics(parsed_jd={"pay": {"raw": "12 LPA"}})


def test_stated_rounds_mean_no_interview_lookup():
    assert INTERVIEW_PROCESS not in _topics()


def test_plan_never_exceeds_the_task_cap():
    assert len(_topics(make_opportunity(compensation=None), parsed_jd={})) <= MAX_TASKS


def test_posting_without_a_company_gets_no_tasks():
    assert _topics(make_opportunity(company=None)) == []


def test_legal_suffixes_are_removed_from_search_names():
    from app.agent.planner import search_name

    assert search_name("InfoCepts Technologies Pvt. Ltd.") == "InfoCepts Technologies"


def test_plain_company_names_are_unchanged():
    from app.agent.planner import search_name

    assert search_name("Zensar") == "Zensar"


def test_searches_use_the_name_without_its_legal_suffix():
    tasks = plan_tasks(
        make_opportunity(company="Acme Private Limited"), STATED_PAY_JD, make_memory()
    )
    assert tasks[0].query == "Acme"
