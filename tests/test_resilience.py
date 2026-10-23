"""Tests for resilient collection behaviour.

These cover a real bug: when one endpoint failed, the old code silently fell
back to DOM, discarded the other endpoint's data, and still reported success.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.collector.api import ApiError, SupersetApi
from app.jobs import poll as poll_module


class FakeResponse:
    def __init__(self, status, payload=None, text=""):
        self.status = status
        self._payload = payload
        self.text = text

    async def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeRequestContext:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0

    async def get(self, url, headers=None):
        self.calls += 1
        status = self.statuses.pop(0) if self.statuses else 200
        return FakeResponse(status, payload={"ok": True} if status == 200 else None)


def _api(statuses) -> tuple[SupersetApi, FakeRequestContext]:
    ctx = FakeRequestContext(statuses)
    api = SupersetApi(
        context=SimpleNamespace(request=ctx), base_url="https://x", headers={"a": "b"}
    )
    api.retry_delay = 0
    return api, ctx


def test_retries_on_server_error_then_succeeds():
    api, ctx = _api([500, 502, 200])
    assert asyncio.run(api._get("/thing")) == {"ok": True}
    assert ctx.calls == 3


def test_raises_after_exhausting_attempts():
    api, ctx = _api([500, 500, 500])
    with pytest.raises(ApiError) as excinfo:
        asyncio.run(api._get("/thing"))
    assert "500" in str(excinfo.value)
    assert ctx.calls == 3


def test_does_not_retry_client_errors():
    """A 401 will never fix itself; retrying just wastes calls."""
    api, ctx = _api([401, 200])
    with pytest.raises(ApiError):
        asyncio.run(api._get("/thing"))
    assert ctx.calls == 1


def test_partial_collection_is_reported_as_partial(monkeypatch):
    """Announcements succeed, jobs fail -> persist what we have, flag partial."""
    announcements = [SimpleNamespace(external_id="a1", title="Notice", body="",
                                      url=None, category=None, published_at=None,
                                      posted_by=None, raw={})]

    async def fake_announcements(api):
        return announcements

    async def fake_jobs(api, **kwargs):
        raise ApiError("GET /job_profiles -> 500")

    class FakeBrowser:
        def __init__(self, *a, **k):
            self._context = object()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    async def fake_api_from_context(context, settings):
        return SimpleNamespace(base_url="https://x", user_uuid="u")

    monkeypatch.setattr(poll_module, "BrowserSession", FakeBrowser)
    monkeypatch.setattr(poll_module, "SupersetApi", SimpleNamespace(
        from_context=staticmethod(fake_api_from_context)))
    monkeypatch.setattr(poll_module, "collect_announcements_api", fake_announcements)
    monkeypatch.setattr(poll_module, "collect_job_profiles_api", fake_jobs)

    settings = SimpleNamespace(
        collector_mode="auto", superset_base_url="https://app.joinsuperset.com",
        superset_notifications_path="/students",
        superset_job_profiles_path="/students/jobprofiles",
        fetch_job_details=False, detail_limit=0, job_status_filter="",
        job_search="", job_tab="",
    )

    got_ann, got_jobs, errors = asyncio.run(poll_module._collect(settings))

    assert len(got_ann) == 1           # announcements were kept
    assert got_jobs == []              # jobs failed
    assert any("job profiles fetch failed" in e for e in errors)


def test_all_failed_still_reports_errors(monkeypatch):
    async def boom(api):
        raise ApiError("GET /x -> 500")

    class FakeBrowser:
        def __init__(self, *a, **k):
            self._context = object()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    async def fake_api_from_context(context, settings):
        return SimpleNamespace(base_url="https://x", user_uuid="u")

    monkeypatch.setattr(poll_module, "BrowserSession", FakeBrowser)
    monkeypatch.setattr(poll_module, "SupersetApi", SimpleNamespace(
        from_context=staticmethod(fake_api_from_context)))
    monkeypatch.setattr(poll_module, "collect_announcements_api", boom)
    monkeypatch.setattr(poll_module, "collect_job_profiles_api", boom)

    settings = SimpleNamespace(
        collector_mode="auto", superset_base_url="https://app.joinsuperset.com",
        superset_notifications_path="/students",
        superset_job_profiles_path="/students/jobprofiles",
        fetch_job_details=False, detail_limit=0, job_status_filter="",
        job_search="", job_tab="",
    )

    _ann, _jobs, errors = asyncio.run(poll_module._collect(settings))
    assert len(errors) == 2