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


def test_application_status_change_is_saved_without_a_content_change(session):
    from app.collector.change_detector import upsert_opportunity

    data = {"external_id": "job-9", "title": "Intern", "raw": {"application_status": "YET_TO_APPLY"}}
    record, _ = upsert_opportunity(session, data)
    upsert_opportunity(session, {**data, "raw": {"application_status": "APPLIED"}})
    assert record.raw["application_status"] == "APPLIED"


def test_stored_parsed_jd_survives_a_later_poll(session):
    from app.collector.change_detector import upsert_opportunity

    data = {"external_id": "job-9", "title": "Intern", "raw": {"application_status": "YET_TO_APPLY"}}
    record, _ = upsert_opportunity(session, data)
    record.raw = {**record.raw, "parsed_jd": {"skills": ["Python"]}}
    upsert_opportunity(session, {**data, "title": "Intern (updated)"})
    assert record.raw["parsed_jd"] == {"skills": ["Python"]}


def test_known_attachments_are_kept_when_a_poll_skips_details(session):
    from app.collector.change_detector import upsert_opportunity

    files = [{"name": "JD.pdf", "identifier": "doc-1"}]
    data = {"external_id": "job-9", "title": "Intern", "raw": {}, "attachments": files}
    record, _ = upsert_opportunity(session, data)
    upsert_opportunity(session, {**data, "attachments": []})
    assert record.attachments == files
