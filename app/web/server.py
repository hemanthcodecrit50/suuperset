"""Local web interface: inbox, briefs, approvals, applications and profile."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings, get_settings
from app.jobs.progress import PollProgress, read_progress
from app.models import Opportunity, ResumeSuggestion
from app.services import fit, notices, portfolio, preferences, profile_insight
from app.services.applications import STATUSES, track
from app.services.events import SOURCES, EventLog, read_events
from app.services.llm import LlmClient
from app.services.resume_import import (
    MAX_PDF_BYTES,
    ResumeReadError,
    extract_text,
    parse_resume,
)
from app.ui.common import days_until, fmt_date, relative
from app.web import queries
from app.web.brief_html import brief_sources, brief_to_html
from app.web.inspector import (
    build_agent_run,
    build_collector_run,
    group_events,
)
from app.web.jobs import Job, JobRunner
from app.web.notice_html import notice_to_html
from app.web.superset_session import disconnect, session_status

WEB_DIR = Path(__file__).parent
JOB_LOG_DIR = Path(".cache/web-jobs")
DEV_MODE_COOKIE = "dev_mode"
UPDATE_VIEWS = ("applied", "all", "jobs")
ACTIVITY_LIMIT = 300
LIST_FIELDS = ("skills", "technologies", "interests", "locations", "companies")
MAX_LIST_ITEMS = 50
CGPA_RANGE = (0.0, 10.0)
GRADUATION_YEAR_RANGE = (2000, 2100)
FONT_SAFE_CHARACTERS = str.maketrans(
    {" ": " ", " ": " ", "‑": "-", "‐": "-"}
)


class FormError(ValueError):
    """A form value the student needs to correct; the message is shown on the page."""

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
    templates = _templates()
    _register_error_page(app, templates)
    _register_routes(app, templates)
    return app


def _register_error_page(app: FastAPI, templates: Jinja2Templates) -> None:
    async def error_page(request: Request, exc: StarletteHTTPException) -> Response:
        # Browsers opening a page get a readable page; htmx and API callers keep JSON
        wants_page = "text/html" in request.headers.get("accept", "")
        if request.headers.get("hx-request") or not wants_page:
            return await http_exception_handler(request, exc)
        context = {"status_code": exc.status_code, "message": exc.detail}
        return templates.TemplateResponse(
            request, "error.html", context, status_code=exc.status_code
        )

    app.exception_handler(StarletteHTTPException)(error_page)


def _templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=WEB_DIR / "templates")
    templates.env.finalize = _font_safe
    templates.env.filters["company"] = queries.clean_company
    templates.env.filters["safe_url"] = safe_url
    templates.env.filters["relative"] = relative
    templates.env.filters["date"] = fmt_date
    templates.env.filters["markdown"] = brief_to_html
    templates.env.filters["pretty"] = lambda value: str(value or "").replace("_", " ")
    templates.env.filters["notice_html"] = notice_to_html
    templates.env.filters["initials"] = _initials
    templates.env.filters["hue"] = _hue
    return templates


def _font_safe(value: Any) -> Any:
    # The bundled font lacks these characters, so the browser would switch fonts mid-word
    if isinstance(value, Markup):
        return Markup(str(value).translate(FONT_SAFE_CHARACTERS))
    if isinstance(value, str):
        return value.translate(FONT_SAFE_CHARACTERS)
    return value


def safe_url(url: str | None) -> str:
    """The link if it is a web address, else "#", so a stored link can never run script."""
    text = str(url or "").strip()
    return text if text.lower().startswith(("http://", "https://")) else "#"


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
        context["updates_count"] = notices.recent_applied_count(session)
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
        # Only polls record stages; other collector events (profile re-reads) are not polls
        latest = read_events(
            events_path(request), source="collector", name="stage", limit=1
        )
        if not latest or latest[0].run_id is None:
            return None
        run_id = latest[0].run_id
        events = list(reversed(read_events(events_path(request), run_id=run_id)))
        return build_collector_run(run_id, events, is_poll_running(request))

    def is_poll_running(request: Request) -> bool:
        job = request.app.state.jobs.get("poll")
        return job is not None and job.state == "running"

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
            upcoming=notices.upcoming_events(session, limit=3),
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
            related=notices.related_notices(session, record.company_name),
            read_documents={
                document["name"]
                for document in (record.raw or {}).get("jd_documents") or []
            },
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
        context = {
            "collector": collector_view(request),
            "poll_running": is_poll_running(request),
        }
        return templates.TemplateResponse(request, "_collector.html", context)

    @app.get("/jobs/{name}", response_class=HTMLResponse)
    def job_status(request: Request, name: str):
        return job_panel(request, request.app.state.jobs.get(name), name)

    @app.get("/updates", response_class=HTMLResponse)
    def updates(request: Request, session: DbSession, view: str = "applied"):
        view = view if view in UPDATE_VIEWS else "applied"
        return page(
            request,
            session,
            "updates",
            view=view,
            views=UPDATE_VIEWS,
            feed=notices.notice_feed(session, only_applied=view == "applied")
            if view != "jobs"
            else [],
            job_alerts=queries.job_alerts(session) if view == "jobs" else [],
            upcoming=notices.upcoming_events(session),
            companies=notices.applied_companies(session),
        )

    @app.get("/approvals", response_class=HTMLResponse)
    def approvals(request: Request, session: DbSession):
        return page(
            request,
            session,
            "approvals",
            pending=queries.suggestions(session, is_approved=False),
            approved=queries.suggestions(session, is_approved=True),
        )

    # Reload after a decision so the approved list and the nav count stay correct
    @app.post("/approvals/{suggestion_id}/approve", response_class=HTMLResponse)
    def approve(session: DbSession, suggestion_id: int):
        _suggestion_or_404(session, suggestion_id).approved = True
        session.commit()
        return HTMLResponse("", headers={"HX-Refresh": "true"})

    @app.post("/approvals/{suggestion_id}/reject", response_class=HTMLResponse)
    def reject(session: DbSession, suggestion_id: int):
        session.delete(_suggestion_or_404(session, suggestion_id))
        session.commit()
        return HTMLResponse("", headers={"HX-Refresh": "true"})

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
    def profile(
        request: Request,
        session: DbSession,
        imported: int | None = None,
        saved: bool = False,
    ):
        return profile_page(request, session, imported=imported, saved=saved)

    def profile_page(
        request: Request, session: Session, status_code: int = 200, **extra: Any
    ) -> HTMLResponse:
        runs = queries.recent_poll_runs(session)
        settings = request.app.state.settings
        response = page(
            request,
            session,
            "profile",
            **extra,
            matching=preferences.load_preferences(session).matching,
            insight=preferences.load_preferences(session).insight,
            projects=portfolio.list_items(session),
            project_statuses=portfolio.STATUSES,
            superset=session_status(
                settings.storage_state_path, runs[0] if runs else None
            ),
            runs=runs,
            login_job=request.app.state.jobs.get("login"),
            poll_job=request.app.state.jobs.get("poll"),
            collector=collector_view(request) if is_dev_mode(request) else None,
            poll_running=is_poll_running(request),
            progress=poll_progress(request, request.app.state.jobs.get("poll")),
        )
        response.status_code = status_code
        return response

    @app.post("/profile/resume", response_class=HTMLResponse)
    async def import_resume(request: Request, session: DbSession, resume: UploadFile):
        settings = request.app.state.settings
        try:
            text = extract_text(await resume.read(MAX_PDF_BYTES + 1))
        except ResumeReadError as exc:
            return profile_page(request, session, resume_error=str(exc))
        llm = LlmClient(settings=settings, events=EventLog(events_path(request)))
        draft = parse_resume(text, llm)
        current = preferences.load_preferences(session).matching
        return page(request, session, "resume_review", draft=draft, current=current)

    @app.post("/profile/resume/apply")
    async def apply_resume(request: Request, session: DbSession):
        form = await request.form()
        try:
            matching = _matching_from_form(form)
        except FormError as exc:
            return profile_page(request, session, 400, form_error=str(exc))
        preferences.update_matching(session, **matching)
        added = _add_selected_projects(session, form)
        refresh_understanding(request, session)
        session.commit()
        return RedirectResponse(f"/profile?imported={added}", status_code=303)

    @app.post("/profile")
    async def save_profile(request: Request, session: DbSession):
        form = await request.form()
        try:
            matching = _matching_from_form(form)
        except FormError as exc:
            return profile_page(request, session, 400, form_error=str(exc))
        preferences.update_matching(session, **matching)
        refresh_understanding(request, session)
        session.commit()
        return RedirectResponse("/profile?saved=1", status_code=303)

    @app.post("/profile/insight/remove")
    def remove_insight(
        request: Request, session: DbSession, item: Annotated[str, Form()]
    ):
        profile_insight.remove_insight_item(session, item)
        fit.rescore_all(session)
        session.commit()
        return RedirectResponse("/profile#understanding", status_code=303)

    @app.post("/profile/insight/refresh")
    def reread_profile(request: Request, session: DbSession):
        profile_insight.forget_profile_insight(session)
        refresh_understanding(request, session)
        session.commit()
        return RedirectResponse("/profile#understanding", status_code=303)

    def refresh_understanding(request: Request, session: Session) -> None:
        # One AI request when the profile changed, then every posting is rescored
        llm = LlmClient(
            settings=request.app.state.settings, events=EventLog(events_path(request))
        )
        profile_insight.refresh_profile_insight(session, llm)
        fit.rescore_all(session)

    @app.post("/portfolio")
    def add_project(
        request: Request,
        session: DbSession,
        name: Annotated[str, Form()],
        description: Annotated[str, Form()] = "",
        technologies: Annotated[str, Form()] = "",
        evidence_url: Annotated[str, Form()] = "",
        status: Annotated[str, Form()] = "implemented",
    ):
        problem = _project_problem(name, evidence_url, status)
        if problem:
            return profile_page(request, session, 400, project_error=problem)
        portfolio.add_item(
            session,
            name=name,
            description=description,
            technologies=technologies,
            evidence_url=evidence_url,
            status=status,
        )
        refresh_understanding(request, session)
        session.commit()
        return RedirectResponse("/profile#portfolio", status_code=303)

    @app.post("/portfolio/{item_id}/delete")
    def delete_project(request: Request, session: DbSession, item_id: int):
        portfolio.delete_item(session, item_id)
        refresh_understanding(request, session)
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


def _project_problem(name: str, evidence_url: str, status: str) -> str | None:
    if not name.strip():
        return "Give the project a name."
    if status not in portfolio.STATUSES:
        return f"Unknown project status {status!r}."
    if evidence_url.strip() and safe_url(evidence_url) == "#":
        return "The evidence link must start with http:// or https://."
    return None


def _add_selected_projects(session: Session, form: Any) -> int:
    existing = {item.name.strip().lower() for item in portfolio.list_items(session)}
    added = 0
    for index in range(_project_count(form)):
        name = str(form.get(f"project-{index}-name") or "").strip()
        if (
            not form.get(f"project-{index}-include")
            or not name
            or name.lower() in existing
        ):
            continue
        link = safe_url(str(form.get(f"project-{index}-link") or ""))
        portfolio.add_item(
            session,
            name=name,
            description=str(form.get(f"project-{index}-description") or ""),
            technologies=str(form.get(f"project-{index}-technologies") or ""),
            evidence_url=None if link == "#" else link,
        )
        existing.add(name.lower())
        added += 1
    return added


def _matching_from_form(form: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        name: _split_list(str(form.get(name, ""))) for name in LIST_FIELDS
    }
    values["degree"] = str(form.get("degree", "")).strip() or None
    values["cgpa"] = _optional_number(form.get("cgpa"), float, "CGPA", CGPA_RANGE)
    values["graduation_year"] = _optional_number(
        form.get("graduation_year"), int, "Graduation year", GRADUATION_YEAR_RANGE
    )
    return values


def _project_count(form: Any) -> int:
    try:
        return max(0, int(form.get("project_count") or 0))
    except ValueError:
        return 0


def _split_list(text: str) -> list[str]:
    items = [part.strip() for part in text.replace("\n", ",").split(",")]
    return [item for item in items if item][:MAX_LIST_ITEMS]


def _optional_number(
    value: Any, kind: type, label: str, allowed: tuple[float, float]
) -> float | int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        number = kind(text)
    except ValueError as exc:
        raise FormError(f"{label}: {text!r} is not a valid number.") from exc
    low, high = allowed
    if not low <= number <= high:
        raise FormError(f"{label} must be between {low:g} and {high:g}.")
    return number
