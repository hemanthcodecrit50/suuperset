from __future__ import annotations

from app.collector.change_detector import (
    CHANGED,
    NEW,
    UNCHANGED,
    canonical_hash,
    diff_fields,
    enqueue_alert,
    upsert_opportunity,
)


def test_canonical_hash_ignores_whitespace():
    a = {"title": "Software Engineer", "description": "Build   things."}
    b = {"title": "Software  Engineer", "description": "Build things."}
    fields = ["title", "description"]
    assert canonical_hash(a, fields) == canonical_hash(b, fields)


def test_canonical_hash_detects_real_change():
    fields = ["title", "description"]
    a = canonical_hash({"title": "SE", "description": "Build things"}, fields)
    b = canonical_hash({"title": "SE", "description": "Build other things"}, fields)
    assert a != b


def test_diff_fields_reports_changes():
    changes = diff_fields(
        {"deadline": "2026-01-01", "title": "SE"},
        {"deadline": "2026-02-01", "title": "SE"},
        ["deadline", "title"],
    )
    assert set(changes) == {"deadline"}
    assert changes["deadline"]["old"] == "2026-01-01"
    assert changes["deadline"]["new"] == "2026-02-01"


def test_upsert_opportunity_lifecycle(session, source_factory):
    source = source_factory(session)
    data = {
        "external_id": "opp-1",
        "title": "Backend Intern",
        "company_name": "Acme",
        "description": "Work on APIs",
    }

    record, change = upsert_opportunity(session, data, source_id=source.id)
    assert change == NEW
    assert record.content_hash

    _, change = upsert_opportunity(session, data, source_id=source.id)
    assert change == UNCHANGED

    changed_data = {**data, "description": "Work on APIs and services"}
    _, change = upsert_opportunity(session, changed_data, source_id=source.id)
    assert change == CHANGED
    assert len(record.versions) == 1


def test_enqueue_alert_deduplicates(session, source_factory):
    source = source_factory(session)
    record, _ = upsert_opportunity(
        session, {"external_id": "opp-2", "title": "Role"}, source_id=source.id
    )
    kwargs = dict(
        channel="telegram",
        change_type=NEW,
        dedup_key="opportunity:opp-2:new:abc",
        opportunity_id=record.id,
        payload={"title": "Role"},
    )
    assert enqueue_alert(session, **kwargs) is not None
    assert enqueue_alert(session, **kwargs) is None
