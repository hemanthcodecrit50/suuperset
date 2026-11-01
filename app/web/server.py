"""Local web interface: inbox, briefs, approvals, applications and profile."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.jobs.progress import PollProgress, read_progress
from app.models import Opportunity, ResumeSuggestion
from app.services import portfolio, preferences
from app.services.applications import STATUSES, track
from app.services.events import SOURCES, read_events
from app.services.llm import LlmClient
from app.ui.common import days_until, fmt_date, relative
from app.web import queries
from app.web.brief_html import brief_sources, brief_to_html
from app.web.inspector import (
    build_agent_run,
    build_collector_run,
    group_events,
)
from app.web.jobs import Job, JobRunner
from app.web.superset_session import disconnect, session_status

WEB_DIR = Path(__file__).parent
JOB_LOG_DIR = Path(".cache/web-jobs")
DEV_MODE_COOKIE = "dev_mode"
ACTIVITY_LIMIT = 300
LIST_FIELDS = ("skills", "technologies", "interests", "locations", "companies")
MAX_LIST_ITEMS = 50

SessionFactory = Callable[[], Session]


def create_app(
    session_factory: SessionFactory | None = None,
    settings: Settings | None = None,
    job_runner: JobRunner | None = None,
) -> FastAPI:
    """Build the web app; tests pass their own session factory and job runner."""
    if session_factory is None:
        from app.db import get_session_factory

        session_factory = get_session_factory()
    app = FastAPI(title="Placement Copilot", docs_url=None, redoc_url=None)
    app.state.session_factory = session_factory
    app.state.settings = settings or get_settings()
    app.state.jobs = job_runner or JobRunner(JOB_LOG_DIR)
    # Which event-log run each posting's latest agent job writes to
    app.state.agent_runs = {}
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.middleware("http")(_reject_cross_site_posts)
    _register_routes(app, _templates())
    return app


def _templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=WEB_DIR / "templates")
    templates.env.filters["relative"] = relative
    templates.env.filters["date"] = fmt_date
    templates.env.filters["markdown"] = brief_to_html
    templates.env.filters["pretty"] = lambda value: str(value or "").replace("_", " ")
    templates.env.filters["initials"] = _initials
    templates.env.filters["hue"] = _hue
    return templates


def _initials(name: str | None) -> str:
    words = [word for word in str(name or "?").split() if word[0].isalnum()]
    return "".join(word[0] for word in words[:2]).upper() or "?"


def _hue(name: str | None) -> int:
    # A stable colour per company, so the same company always looks the same
    return int(hashlib.md5(str(name or "").encode()).hexdigest()[:4], 16) % 360


async def _reject_cross_site_posts(request: Request, call_next: Any) -> Response:
    # Any page on the internet could otherwise post to this local app
    origin = request.headers.get("origin")
    if (
        request.method == "POST"
        and origin
        and urlparse(origin).netloc != request.url.netloc
    ):
        return Response("Cross-site request blocked.", status_code=403)
    return await call_next(request)


def _session(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


DbSession = Annotated[Session, Depends(_session)]


def _register_routes(app: FastAPI, templates: Jinja2Templates) -> None:
    def page(
        request: Request, session: Session, name: str, **context: Any
    ) -> HTMLResponse:
        settings = request.app.state.settings
        runs = queries.recent_poll_runs(session, limit=1)
        context["pending_count"] = queries.pending_approval_count(session)
        context["active_page"] = name
        context["superset_state"] = session_status(
            settings.storage_state_path, runs[0] if runs else None
        ).state
        context["llm"] = LlmClient(settings=settings)
        context["dev_mode"] = is_dev_mode(request)
        context["is_busy"] = any(
            job.state == "running" for job in request.app.state.jobs.jobs.values()
        )
        return templates.TemplateResponse(request, f"{name}.html", context)

    def is_dev_mode(request: Request) -> bool:
        return request.cookies.get(DEV_MODE_COOKIE) == "1"

    def events_path(request: Request) -> Path:
        return Path(request.app.state.settings.events_file)

    def agent_run_view(request: Request, opportunity_id: int, brief: Any):
        job = request.app.state.jobs.get(f"agent-{opportunity_id}")
        is_running = job is not None and job.state == "running"
        run_id = request.app.state.agent_runs.get(opportunity_id) if job else None
        if run_id is None and brief is not None:
            run_id = brief.content.get("run_id")
        if run_id is None:
            return None
        events = list(reversed(read_events(events_path(request), run_id=run_id)))
        is_latest_brief = brief is not None and brief.content.get("run_id") == run_id
        content = brief.content if is_latest_brief else None
        return build_agent_run(run_id, events, content, is_running)

    def collector_view(request: Request):
        latest = read_events(events_path(request), source="collector", limit=1)
        if not latest or latest[0].run_id is None:
            return None
        run_id = latest[0].run_id
        events = list(reversed(read_events(events_path(request), run_id=run_id)))
        job = request.app.state.jobs.get("poll")
        return build_collector_run(
            run_id, events, job is not None and job.state == "running"
        )

    def job_panel(request: Request, job: Job | None, name: str) -> HTMLResponse:
        context = {
            "job": job,
            "job_name": name,
            "progress": poll_progress(request, job),
        }
        response = templates.TemplateResponse(request, "_job.html", context)
        if job is not None and job.state == "succeeded" and name != "login":
            response.headers["HX-Refresh"] = "true"
        return response

    def poll_progress(request: Request, job: Job | None) -> PollProgress | None:
        if job is None or job.name != "poll":
            return None
        progress = read_progress(Path(request.app.state.settings.poll_progress_file))
        # Ignore what an earlier poll left behind
        if progress is None or progress.updated_at < job.started_at.isoformat():
            return None
        return progress

    @app.get("/", response_class=HTMLResponse)
    def inbox(request: Request, session: DbSession, view: str = "open", q: str = ""):
        view = view if view in queries.INBOX_VIEWS else "open"
        rows = queries.inbox_rows(session, view, q)
        return page(
            request,
            session,
            "inbox",
            rows=rows,
            stats=queries.inbox_stats(session),
            view=view,
            q=q,
            views=queries.INBOX_VIEWS,
        )

    @app.get("/postings/{opportunity_id}", response_class=HTMLResponse)
    def posting(request: Request, session: DbSession, opportunity_id: int):
        record = _opportunity_or_404(session, opportunity_id)
        brief = queries.posting_brief(session, opportunity_id)
        return page(
            request,
            session,
            "posting",
            record=record,
            brief=brief,
            sources=brief_sources(brief.content.get("markdown", "")) if brief else [],
            profile=preferences.load_preferences(session).matching,
            run=agent_run_view(request, opportunity_id, brief)
            if is_dev_mode(request)
            else None,
            status=queries.posting_status(session, record),
            days_left=days_until(record.deadline),
            fit_score=record.relevance_score
            if preferences.load_preferences(session).is_configured()
            else None,
            location=queries.clean_location(record.location),
            portal_url=record.apply_url
            or _portal_url(request.app.state.settings, record),
            statuses=STATUSES,
            job=request.app.state.jobs.get(f"agent-{opportunity_id}"),
        )

    @app.post("/postings/{opportunity_id}/status", response_class=HTMLResponse)
    def set_status(
        session: DbSession, opportunity_id: int, status: Annotated[str, Form()]
    ):
        record = _opportunity_or_404(session, opportunity_id)
        if status not in STATUSES:
            raise HTTPException(400, f"Unknown status {status!r}.")
        track(session, record, status=status)
        session.commit()
        return HTMLResponse('<span class="saved">Saved</span>')

    @app.post("/postings/{opportunity_id}/agent", response_class=HTMLResponse)
    def run_agent(request: Request, session: DbSession, opportunity_id: int):
        _opportunity_or_404(session, opportunity_id)
        name = f"agent-{opportunity_id}"
        existing = request.app.state.jobs.get(name)
        if existing is None or existing.state != "running":
            run_id = f"agent-{opportunity_id}-{int(time.time())}"
            request.app.state.agent_runs[opportunity_id] = run_id
            arguments = [
                "agent",
                "run",
                str(opportunity_id),
                "--fresh",
                "--run-id",
                run_id,
            ]
            existing = request.app.state.jobs.start(name, arguments)
        return job_panel(request, existing, name)

    @app.get("/postings/{opportunity_id}/inspector", response_class=HTMLResponse)
    def inspector(request: Request, session: DbSession, opportunity_id: int):
        brief = queries.posting_brief(session, opportunity_id)
        run = agent_run_view(request, opportunity_id, brief)
        context = {"run": run, "record": _opportunity_or_404(session, opportunity_id)}
        return templates.TemplateResponse(request, "_inspector.html", context)

    @app.post("/dev-mode")
    def toggle_dev_mode(request: Request, next: Annotated[str, Form()] = "/"):
        # Only redirect inside this app
        target = next if next.startswith("/") and not next.startswith("//") else "/"
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(
            DEV_MODE_COOKIE, "0" if is_dev_mode(request) else "1", samesite="lax"
        )
        return response

    @app.get("/activity", response_class=HTMLResponse)
    def activity(request: Request, session: DbSession, source: str = ""):
        return page(request, session, "activity", **activity_context(request, source))

    @app.get("/activity/stream", response_class=HTMLResponse)
    def activity_stream(request: Request, source: str = ""):
        context = activity_context(request, source)
        return templates.TemplateResponse(request, "_activity_stream.html", context)

    def activity_context(request: Request, source: str) -> dict[str, Any]:
        source = source if source in SOURCES else ""
        events = read_events(
            events_path(request), limit=ACTIVITY_LIMIT, source=source or None
        )
        return {"groups": group_events(events), "source": source, "sources": SOURCES}

    @app.get("/collector", response_class=HTMLResponse)
    def collector(request: Request):
        context = {"collector": collector_view(request)}
        return templates.TemplateResponse(request, "_collector.html", context)

    @app.get("/jobs/{name}", response_class=HTMLResponse)
    def job_status(request: Request, name: str):
        return job_panel(request, request.app.state.jobs.get(name), name)

    @app.get("/approvals", response_class=HTMLResponse)
    def approvals(request: Request, session: DbSession):
        return page(
            request,
            session,
            "approvals",
            pending=queries.suggestions(session, is_approved=False),
            approved=queries.suggestions(session, is_approved=True),
        )

    @app.post("/approvals/{suggestion_id}/approve", response_class=HTMLResponse)
    def approve(session: DbSession, suggestion_id: int):
        _suggestion_or_404(session, suggestion_id).approved = True
        session.commit()
        return HTMLResponse("")

    @app.post("/approvals/{suggestion_id}/reject", response_class=HTMLResponse)
    def reject(session: DbSession, suggestion_id: int):
        session.delete(_suggestion_or_404(session, suggestion_id))
        session.commit()
        return HTMLResponse("")

    @app.get("/applications", response_class=HTMLResponse)
    def applications(request: Request, session: DbSession):
        return page(
            request,
            session,
            "applications",
            rows=queries.tracked_applications(session),
            statuses=STATUSES,
        )

    @app.get("/profile", response_class=HTMLResponse)
    def profile(request: Request, session: DbSession):
        runs = queries.recent_poll_runs(session)
        settings = request.app.state.settings
        return page(
            request,
            session,
            "profile",
            matching=preferences.load_preferences(session).matching,
            projects=portfolio.list_items(session),
            project_statuses=portfolio.STATUSES,
            superset=session_status(
                settings.storage_state_path, runs[0] if runs else None
            ),
            runs=runs,
            login_job=request.app.state.jobs.get("login"),
            poll_job=request.app.state.jobs.get("poll"),
            collector=collector_view(request) if is_dev_mode(request) else None,
            progress=poll_progress(request, request.app.state.jobs.get("poll")),
        )

    @app.post("/profile")
    async def save_profile(request: Request, session: DbSession):
        form = await request.form()
        preferences.update_matching(session, **_matching_from_form(form))
        session.commit()
        return RedirectResponse("/profile?saved=1", status_code=303)

    @app.post("/portfolio")
    def add_project(
        session: DbSession,
        name: Annotated[str, Form()],
        description: Annotated[str, Form()] = "",
        technologies: Annotated[str, Form()] = "",
        evidence_url: Annotated[str, Form()] = "",
        status: Annotated[str, Form()] = "implemented",
    ):
        portfolio.add_item(
            session,
            name=name,
            description=description,
            technologies=technologies,
            evidence_url=evidence_url,
            status=status,
        )
        session.commit()
        return RedirectResponse("/profile#portfolio", status_code=303)

    @app.post("/portfolio/{item_id}/delete")
    def delete_project(session: DbSession, item_id: int):
        portfolio.delete_item(session, item_id)
        session.commit()
        return RedirectResponse("/profile#portfolio", status_code=303)

    @app.post("/superset/connect", response_class=HTMLResponse)
    def connect_superset(request: Request):
        return job_panel(
            request, request.app.state.jobs.start("login", ["login"]), "login"
        )

    @app.post("/superset/disconnect")
    def disconnect_superset(request: Request):
        disconnect(request.app.state.settings.storage_state_path)
        return RedirectResponse("/profile", status_code=303)

    @app.post("/poll", response_class=HTMLResponse)
    def poll_now(request: Request):
        return job_panel(
            request, request.app.state.jobs.start("poll", ["poll"]), "poll"
        )


def _portal_url(settings: Settings, record: Opportunity) -> str:
    base = settings.superset_base_url.rstrip("/")
    return (
        f"{base}{settings.superset_job_profiles_path}?currentJobId={record.external_id}"
    )


def _opportunity_or_404(session: Session, opportunity_id: int) -> Opportunity:
    record = session.get(Opportunity, opportunity_id)
    if record is None:
        raise HTTPException(404, "No such posting.")
    return record


def _suggestion_or_404(session: Session, suggestion_id: int) -> ResumeSuggestion:
    suggestion = session.get(ResumeSuggestion, suggestion_id)
    if suggestion is None:
        raise HTTPException(404, "No such suggestion.")
    return suggestion


def _matching_from_form(form: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        name: _split_list(str(form.get(name, ""))) for name in LIST_FIELDS
    }
    values["degree"] = str(form.get("degree", "")).strip() or None
    values["cgpa"] = _optional_number(form.get("cgpa"), float)
    values["graduation_year"] = _optional_number(form.get("graduation_year"), int)
    return values


def _split_list(text: str) -> list[str]:
    items = [part.strip() for part in text.replace("\n", ",").split(",")]
    return [item for item in items if item][:MAX_LIST_ITEMS]


def _optional_number(value: Any, kind: type) -> float | int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return kind(text)
    except ValueError as exc:
        raise HTTPException(400, f"{text!r} is not a valid number.") from exc
