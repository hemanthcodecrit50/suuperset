from __future__ import annotations

from app.services.resume import rank_portfolio_items, skill_gaps, suggest_bullets


PORTFOLIO = [
    {
        "id": 1,
        "name": "Payments API",
        "description": "Built a Django REST API with PostgreSQL",
        "technologies": ["python", "django", "postgres"],
        "achievements": ["served 10k requests/day"],
        "evidence_url": "https://example.com/payments",
    },
    {
        "id": 2,
        "name": "Landing Page",
        "description": "Static marketing page",
        "technologies": ["html", "css"],
        "achievements": [],
        "evidence_url": None,
    },
]


def test_rank_prefers_relevant_item():
    ranked = rank_portfolio_items(["python", "postgres"], PORTFOLIO)
    assert ranked[0][0] == 1


def test_skill_gaps_lists_uncovered_requirements():
    gaps = skill_gaps(["kubernetes"], PORTFOLIO)
    assert "kubernetes" in gaps


def test_suggestions_link_to_evidence_and_need_approval():
    suggestions = suggest_bullets(requirements=["python"], portfolio_items=PORTFOLIO)
    assert suggestions
    top = suggestions[0]
    assert top.portfolio_item_id == 1
    assert top.evidence == "https://example.com/payments"
    assert top.approved is False
