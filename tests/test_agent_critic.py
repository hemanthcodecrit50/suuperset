"""Tests for the critic node."""

from __future__ import annotations

from agent_helpers import FakeTools, make_deps, make_evidence, make_state

from app.agent import critic
from app.agent.state import Claim

URL = "https://en.wikipedia.org/wiki/Zensar"
SNIPPET = (
    "Zensar Technologies is an Indian IT services company founded in 1963 in Pune."
)


def _state_with(claim_text: str, topic: str = "company_overview"):
    state = make_state()
    state.evidence = [make_evidence(SNIPPET, URL)]
    state.claims = [Claim(text=claim_text, source_url=URL, topic=topic)]
    return state


def test_claim_copied_from_its_source_is_verified():
    state = critic.check_claims(_state_with(SNIPPET), make_deps())
    assert state.claims[0].is_verified is True


def test_claim_with_a_number_missing_from_the_source_is_rejected():
    state = critic.check_claims(
        _state_with("Zensar was founded in 1999 in Pune."), make_deps()
    )
    assert state.claims[0].is_verified is False


def test_claim_unrelated_to_its_source_is_rejected():
    claim = "The company raised a large funding round from several investors recently."
    state = critic.check_claims(_state_with(claim), make_deps())
    assert state.claims[0].is_verified is False


def test_claim_citing_a_source_outside_the_evidence_is_rejected():
    state = _state_with(SNIPPET)
    state.claims[0].source_url = "https://unknown.test/page"
    state = critic.check_claims(state, make_deps())
    assert state.claims[0].is_verified is False


def test_full_page_text_can_rescue_a_claim_the_snippet_does_not_cover():
    claim = "Zensar has offices in Pune and employs engineers across several countries."
    page = "Zensar has offices in Pune and employs engineers across several countries."
    deps = make_deps(FakeTools(make_deps().settings, pages={URL: page}))
    state = critic.check_claims(_state_with(claim), deps)
    assert state.claims[0].is_verified is True


def test_claim_stays_rejected_when_the_page_cannot_be_fetched():
    claim = "Zensar has offices in Pune and employs engineers across several countries."
    state = critic.check_claims(_state_with(claim), make_deps())
    assert state.claims[0].is_verified is False


def test_page_checks_stop_at_the_configured_budget():
    tools = FakeTools(make_deps().settings)
    deps = make_deps(tools, agent_max_page_checks=1)
    state = make_state()
    state.evidence = [
        make_evidence(SNIPPET, URL),
        make_evidence(SNIPPET, "https://b.test"),
    ]
    state.claims = [
        Claim(
            text="Unrelated words about something else entirely",
            source_url=URL,
            topic="a",
        ),
        Claim(
            text="More unrelated words about other things",
            source_url="https://b.test",
            topic="b",
        ),
    ]
    critic.check_claims(state, deps)
    assert tools.tool_names().count("fetch_page") == 1


def test_retry_is_requested_when_too_many_claims_fail():
    state = critic.check_claims(
        _state_with("Zensar was founded in 1999 in Pune."), make_deps()
    )
    assert critic.needs_retry(state, make_deps()) is True


def test_retry_is_not_requested_when_every_claim_passes():
    state = critic.check_claims(_state_with(SNIPPET), make_deps())
    assert critic.needs_retry(state, make_deps()) is False


def test_retry_is_not_requested_twice():
    state = critic.check_claims(
        _state_with("Zensar was founded in 1999 in Pune."), make_deps()
    )
    state.retry_count = 1
    assert critic.needs_retry(state, make_deps()) is False


def test_dropping_unsupported_claims_keeps_only_verified_ones():
    state = _state_with(SNIPPET)
    state.claims.append(
        Claim(text="Zensar has 99999 staff", source_url=URL, topic="headcount")
    )
    state = critic.drop_unsupported(critic.check_claims(state, make_deps()))
    assert [claim.text for claim in state.claims] == [SNIPPET]


def test_topic_with_no_verified_claim_is_reported_unknown():
    state = _state_with("Zensar was founded in 1999 in Pune.", topic="founding")
    state = critic.drop_unsupported(critic.check_claims(state, make_deps()))
    assert "No verified source for: founding" in state.unknowns


def test_bullet_grounded_in_the_project_record_is_kept():
    state = make_state()
    state.resume_advice = {
        "bullet_rewrites": [
            {
                "project_id": 1,
                "requirement": "Python",
                "bullet": "Backend REST service for orders built with Python and PostgreSQL",
            }
        ]
    }
    critic.check_claims(state, make_deps())
    assert len(state.resume_advice["bullet_rewrites"]) == 1


def test_bullet_with_an_invented_metric_is_dropped():
    state = make_state()
    state.resume_advice = {
        "bullet_rewrites": [
            {
                "project_id": 1,
                "requirement": "Python",
                "bullet": "Built a Python service handling 5000000 requests",
            }
        ]
    }
    critic.check_claims(state, make_deps())
    assert state.resume_advice["bullet_rewrites"] == []
