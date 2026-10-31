"""Tests for the Week 3 resume approval gate."""

from __future__ import annotations

from agent_helpers import make_deps, make_state

from app.agent.approval import approval_gate
from app.models import ResumeSuggestion


def _with_bullets(state):
    state.resume_advice = {
        "bullet_rewrites": [
            {
                "project_id": 1,
                "requirement": "Python",
                "bullet": "Built an order service with Python",
            },
            {
                "project_id": 1,
                "requirement": "PostgreSQL",
                "bullet": "Implemented order persistence with PostgreSQL",
            },
        ]
    }
    return state


def test_approval_gate_without_bullets_is_a_no_op(session):
    state = make_state()
    deps = make_deps()
    deps.session = session

    approval_gate(state, deps)

    assert state.pending_approvals == 0
    assert session.query(ResumeSuggestion).count() == 0


def test_approval_gate_persists_unapproved_bullets(session):
    state = _with_bullets(make_state())
    deps = make_deps()
    deps.session = session

    approval_gate(state, deps)

    rows = session.query(ResumeSuggestion).order_by(ResumeSuggestion.id).all()
    assert state.pending_approvals == 2
    assert len(rows) == 2
    assert all(row.approved is False for row in rows)
    assert rows[0].evidence == "https://github.com/me/order-service"


def test_approval_gate_deduplicates_repeated_bullets(session):
    state = _with_bullets(make_state())
    deps = make_deps()
    deps.session = session

    approval_gate(state, deps)
    approval_gate(state, deps)

    assert session.query(ResumeSuggestion).count() == 2
    assert state.pending_approvals == 0
