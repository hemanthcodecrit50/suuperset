"""Command-line entry point for the placement intelligence system.

Examples::

    python -m app.main init-db     # create tables in the configured database
    python -m app.main login       # interactive SSO login, saves browser state
    python -m app.main poll        # run one collection cycle
    python -m app.main deliver     # drain the alert queue
    python -m app.main heartbeat   # raise an alert if polling is unhealthy
    python -m app.main stats       # show recent poll-run health
    python -m app.main tools       # list the agent tools (week 1)
    python -m app.main tool NAME   # call one agent tool with JSON arguments
    python -m app.main agent run ID  # run the agent graph on one opportunity
    python -m app.main resume-review  # review pending resume suggestions
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db import create_all, session_scope
from app.models import PollRun


def _cmd_init_db(_args: argparse.Namespace) -> int:
    logging.basicConfig(level=get_settings().log_level)
    create_all()
    print("Database tables created.")
    return 0


def _cmd_login(_args: argparse.Namespace) -> int:
    from scripts.login import run_login

    return asyncio.run(run_login())


def _cmd_poll(_args: argparse.Namespace) -> int:
    from app.jobs.poll import main as poll_main

    return poll_main()


def _cmd_deliver(_args: argparse.Namespace) -> int:
    from app.services.alerts import deliver_pending

    with session_scope() as session:
        results = deliver_pending(session)
    sent = sum(1 for r in results if r.ok)
    print(f"Delivered {sent}/{len(results)} alerts.")
    return 0


def _cmd_dryrun(args: argparse.Namespace) -> int:
    """Collect from the live portal and print JSON without writing to the DB."""
    import asyncio
    import json
    import logging
    from dataclasses import asdict

    from app.collector.api import SupersetApi
    from app.collector.browser import BrowserSession
    from app.collector.job_profiles import collect_job_profiles
    from app.collector.mappers import collect_announcements_api, collect_job_profiles_api
    from app.collector.notifications import collect_announcements

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else get_settings().log_level,
        format="%(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    status = args.status or settings.job_status_filter or None
    scroll_limit = args.detail_limit or settings.detail_limit or None

    async def _run() -> dict:
        async with BrowserSession(settings=settings, headless=True) as session:
            if settings.collector_mode != "dom":
                try:
                    api = await SupersetApi.from_context(session._context, settings)
                    announcements = await collect_announcements_api(api)
                    opportunities = await collect_job_profiles_api(
                        api,
                        include_detail=settings.fetch_job_details,
                        detail_limit=scroll_limit,
                        status=status,
                    )
                    return {
                        "mode": "api",
                        "announcements": [asdict(a) for a in announcements],
                        "opportunities": [asdict(o) for o in opportunities],
                    }
                except Exception as exc:  # noqa: BLE001 - fall back to DOM
                    logging.getLogger(__name__).warning(
                        "API collection failed (%s); falling back to DOM.", exc
                    )

            ann_page = await session.goto(
                settings.superset_base_url.rstrip("/") + settings.superset_notifications_path,
                expect_app=True,
            )
            announcements = await collect_announcements(ann_page, max_scrolls=args.scrolls)

            job_page = await session.goto(
                settings.superset_base_url.rstrip("/") + settings.superset_job_profiles_path,
                expect_app=True,
            )
            opportunities = await collect_job_profiles(
                job_page,
                search=args.search or settings.job_search or None,
                status=status,
                tab=args.tab or settings.job_tab or None,
                load_more_scrolls=args.scrolls,
            )
            return {
                "mode": "dom",
                "announcements": [asdict(a) for a in announcements],
                "opportunities": [asdict(o) for o in opportunities],
            }

    data = asyncio.run(_run())
    print(json.dumps(data, indent=2, default=str))
    print(
        f"[dry-run mode={data['mode']}] "
        f"announcements={len(data['announcements'])} "
        f"opportunities={len(data['opportunities'])}",
        file=sys.stderr,
    )
    return 0


def _cmd_heartbeat(_args: argparse.Namespace) -> int:
    from app.jobs.poll import check_heartbeat

    raised = check_heartbeat()
    print("Heartbeat warning raised." if raised else "Heartbeat healthy.")
    return 0


def _cmd_stats(_args: argparse.Namespace) -> int:
    with session_scope() as session:
        runs = session.scalars(
            select(PollRun).order_by(PollRun.id.desc()).limit(10)
        ).all()
        if not runs:
            print("No poll runs yet.")
            return 0
        print(f"{'id':>5} {'started':<25} {'status':<8} {'seen':>5} {'new':>5} {'chg':>5} {'alerts':>6}")
        for run in runs:
            started = run.started_at.isoformat() if run.started_at else ""
            print(
                f"{run.id:>5} {started:<25} {run.status:<8} "
                f"{run.items_seen:>5} {run.items_new:>5} {run.items_changed:>5} "
                f"{run.alerts_queued:>6}"
            )
    return 0


def _cmd_migrate(_args: argparse.Namespace) -> int:
    from app.db import migrate

    applied = migrate()
    if applied:
        print(f"Applied {len(applied)} column migration(s):")
        for item in applied:
            print(f"  + {item}")
    else:
        print("Schema already up to date.")
    return 0


def _cmd_prefs(args: argparse.Namespace) -> int:
    from app.services.preferences import (
        KEY_MATCHING,
        KEY_NOTIFICATIONS,
        load_preferences,
        update_matching,
        update_notifications,
    )

    def split(value: str | None) -> list[str] | None:
        """Split a preference list.

        Commas separate items; whitespace only if there is no comma. That lets
        ``--skills "python sql"`` work while ``--work-types "full time,
        internship"`` keeps "full time" intact.
        """
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        if "," in text:
            parts = [p.strip() for p in text.split(",")]
        else:
            parts = text.split()
        return [p for p in parts if p] or None

    if args.action == "show":
        with session_scope() as session:
            prefs = load_preferences(session)
            print("Matching preferences:")
            if prefs.matching:
                for key, value in prefs.matching.items():
                    print(f"  {key}: {value}")
            else:
                print("  (none set - run: prefs set --skills 'python sql')")
            print("\nNotification preferences:")
            if prefs.notifications:
                for key, value in prefs.notifications.items():
                    print(f"  {key}: {value}")
            else:
                print("  (defaults in use)")
        return 0

    if args.action == "set":
        with session_scope() as session:
            if args.clear_matching:
                update_matching(session, clear=True)
            if args.clear_notifications:
                update_notifications(session, clear=True)
            matching = update_matching(
                session,
                skills=split(args.skills),
                interests=split(args.interests),
                technologies=split(args.technologies),
                work_types=split(args.work_types),
                locations=split(args.locations),
                companies=split(args.companies),
                min_compensation=args.min_compensation,
                degree=args.degree,
                cgpa=args.cgpa,
                graduation_year=args.graduation_year,
            )
            notifications = update_notifications(
                session,
                min_alert_score=args.min_alert_score,
                always_alert_changes=args.always_alert_changes,
                deadline_reminder_days=[int(d) for d in split(args.reminder_days) or []]
                if args.reminder_days
                else None,
                daily_digest=args.daily_digest,
                digest_hour=args.digest_hour,
            )
        print("Updated matching preferences:")
        for key, value in matching.items():
            print(f"  {key}: {value}")
        print("\nUpdated notification preferences:")
        for key, value in notifications.items():
            print(f"  {key}: {value}")
        print("\nTip: only jobs scoring >= min_alert_score will alert you.")
        print("Lists: separate with commas for multi-word values, "
              'e.g. --work-types "full time, internship"')
        return 0

    return 0


def _cmd_track(args: argparse.Namespace) -> int:
    from app.services.applications import STATUSES, find_opportunity, track

    if args.action == "list":
        from app.services.applications import open_applications

        with session_scope() as session:
            rows = open_applications(session)
            if not rows:
                print("No tracked applications.")
                return 0
            print(f"{'id':>4}  {'status':<14} {'company':<24} title")
            for application, opp in rows:
                print(
                    f"{application.id:>4}  {application.status:<14} "
                    f"{(opp.company_name or '-')[:23]:<24} {opp.title[:40]}"
                )
        return 0

    # set
    with session_scope() as session:
        opportunity = find_opportunity(session, args.query or "", args.id)
        if opportunity is None:
            print(f"No opportunity matched {args.query!r} (or id {args.id}).")
            return 1
        result = track(
            session,
            opportunity,
            status=args.status,
            priority=args.priority,
            next_action=args.next_action,
            next_action_at=args.on,
            notes=args.notes,
            mark_applied=args.mark_applied,
        )
        print(
            f"{'Created' if result.created else 'Updated'} application "
            f"#{result.application.id}: {opportunity.title} "
            f"({opportunity.company_name or 'unknown company'}) -> {args.status}"
        )
    return 0


def _cmd_remind(args: argparse.Namespace) -> int:
    from app.jobs.remind import run_reminders

    result = run_reminders()
    print(
        f"Deadline reminders: checked={result.checked} "
        f"reminded={result.reminded} (deduped)"
    )
    return 0


def _cmd_digest(args: argparse.Namespace) -> int:
    from app.jobs.digest import run_digest

    result = run_digest(force=args.force)
    if result.sent:
        print("Daily digest queued.")
    else:
        print(f"Digest skipped ({result.reason}).")
    return 0


def _cmd_research(args: argparse.Namespace) -> int:
    from app.services.research import generate_and_store

    report = generate_and_store(args.company, args.role, force=args.force)
    print(f"\n=== {args.company} ===")
    print(f"summary: {report.summary}")
    print("\n--- sections ---")
    for name, content in report.sections.items():
        if content:
            print(f"{name}: {content}")
    print("\n--- sources ---")
    for item in report.evidence:
        print(f"  [{item.source_type}] {item.source_url}")
        print(f"      {item.claim[:100]}")
    return 0


def _cmd_resume(args: argparse.Namespace) -> int:
    from app.services.resume import suggest_for_opportunity

    with session_scope() as session:
        opportunity = None
        if args.id:
            from app.models import Opportunity

            opportunity = session.get(Opportunity, args.id)
        elif args.query:
            from app.services.applications import find_opportunity

            opportunity = find_opportunity(session, args.query)
        if opportunity is None:
            print("Could not resolve the opportunity (use --id or --query).")
            return 1

        suggestions = suggest_for_opportunity(session, opportunity, use_model=not args.no_model)
        print(f"\n=== Resume suggestions for: {opportunity.title} ===")

        from app.models import PortfolioItem
        from app.services.resume import _requirements_from_opportunity

        item_count = len(session.scalars(select(PortfolioItem)).all())
        requirements = _requirements_from_opportunity(opportunity)

        if item_count == 0:
            print(
                "No portfolio items yet. Add your projects on the Portfolio "
                "page — suggestions must be grounded in real experience."
            )
            return 0

        if not requirements:
            print(
                "This job has no published JD or skills, so there is nothing "
                "to ground a suggestion in."
            )
            return 0
        if not suggestions:
            print(
                f"{item_count} portfolio item(s) exist, but none overlap this "
                f"job's {len(requirements)} requirement(s). Nothing is suggested "
                "rather than inventing a match."
            )
            return 0

        print(f"Based on {item_count} portfolio item(s).")
        for item in suggestions:
            print(f"\n- requirement: {item.requirement}")
            print(f"  bullet    : {item.bullet}")
            print(f"  evidence  : {item.evidence or 'MISSING - do not use'}")
            print(f"  approved  : {item.approved}")
    return 0


def _cmd_resume_review(args: argparse.Namespace) -> int:
    """List pending bullets and optionally approve selected suggestions."""
    from app.models import ResumeSuggestion

    with session_scope() as session:
        for suggestion_id in args.approve or []:
            suggestion = session.get(ResumeSuggestion, suggestion_id)
            if suggestion is None:
                print(f"No resume suggestion with id {suggestion_id}.")
                return 1
            suggestion.approved = True
        pending = session.scalars(
            select(ResumeSuggestion)
            .where(ResumeSuggestion.approved.is_(False))
            .order_by(ResumeSuggestion.id)
        ).all()
        if args.approve:
            print(f"Approved {len(args.approve)} resume suggestion(s).")
        if not pending:
            print("No pending resume suggestions.")
            return 0
        print("Pending resume suggestions:")
        for item in pending:
            print(f"[{item.id}] {item.bullet}")
            if item.requirement:
                print(f"    Requirement: {item.requirement}")
        print("Approve with: python -m app.main resume-review --approve ID")
    return 0


def _cmd_telegram_chat_id(_args: argparse.Namespace) -> int:
    """Print recent chat ids the bot can see, so you can fill in CHAT_ID.

    Send your bot any message first (e.g. /start), then run this.
    """
    import httpx

    settings = get_settings()
    if not settings.telegram_bot_token:
        print("Set TELEGRAM_BOT_TOKEN in .env first (from @BotFather).")
        return 1

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/getUpdates"
    try:
        response = httpx.get(url, timeout=20.0)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - network/user error
        print(f"Could not reach Telegram: {exc}")
        return 1

    updates = response.json().get("result") or []
    chats: dict[int, str] = {}
    for update in updates:
        for key in ("message", "edited_message", "channel_post"):
            chat = (update.get(key) or {}).get("chat") or {}
            if chat.get("id"):
                chats[int(chat["id"])] = chat.get("title") or chat.get("username") or ""

    if not chats:
        print("No updates found. Open Telegram, send your bot a message (e.g. /start), "
              "then run this again.")
        return 0

    print("Set TELEGRAM_CHAT_ID in .env to one of:\n")
    for chat_id, label in chats.items():
        print(f"  {chat_id}   {label}")
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    """Inject a fake posting to test the agent -> document -> Telegram path.

    Creates a clearly-marked DEMO record, writes the research document, queues
    the alert, and (with --send) delivers it to Telegram. Use --cleanup to
    remove the demo records and their documents afterwards.
    """
    import datetime as dt
    import shutil

    from app.agent.research_agent import build_document, format_telegram, write_document
    from app.collector.change_detector import CHANGED, NEW, enqueue_alert
    from app.db import get_engine
    from app.models import AlertDelivery, Opportunity, OpportunityVersion
    from sqlalchemy import select

    settings = get_settings()
    now = dt.datetime.now(dt.timezone.utc)
    marker = "DEMO"

    # --- cleanup -------------------------------------------------------
    if args.cleanup:
        with session_scope() as session:
            demo_ids = list(
                session.scalars(
                    select(Opportunity.id).where(
                        Opportunity.external_id.like("demo-%")
                    )
                ).all()
            )
            for opportunity_id in demo_ids:
                session.query(AlertDelivery).filter(
                    AlertDelivery.opportunity_id == opportunity_id
                ).delete(synchronize_session=False)
            deleted = (
                session.query(Opportunity)
                .filter(Opportunity.external_id.like("demo-%"))
                .delete(synchronize_session=False)
            )
        shutil.rmtree("research", ignore_errors=True)
        print(f"Removed {deleted} demo record(s), their alerts and research/.")
        return 0

    # --- create a brand new posting ------------------------------------
    with session_scope() as session:
        existing = session.scalar(
            select(Opportunity).where(
                Opportunity.external_id == "demo-new-posting"
            )
        )
        if existing is not None:
            print("Demo posting already exists. Use --cleanup first.")
            return 1

        record = Opportunity(
            external_id="demo-new-posting",
            title="DEMO Backend Engineer Intern",
            company_name="Demo Corp",
            location="Bengaluru",
            work_type="Internship",
            compensation="Effective 120,000 INR (month)",
            eligibility="Eligible",
            job_function="Engineering",
            description=(
                "You will build backend services with Python and PostgreSQL.\n"
                "Requirements: problem solving and communication skills."
            ),
            apply_url="https://app.joinsuperset.com/students/jobprofiles",
            deadline=now + dt.timedelta(days=5),
            content_hash="demo-hash-1",
            first_seen_at=now,
            last_seen_at=now,
            raw={"application_status": args.status, "category": "Tier 2"},
        )
        session.add(record)
        session.flush()

        document = build_document(record, NEW, now=now)
        path = write_document(document, record.company_name, record.title, now=now)
        message = format_telegram(record, NEW, document_path=path, now=now)
        enqueue_alert(
            session,
            channel="telegram",
            change_type=NEW,
            dedup_key=f"demo:{record.id}:new",
            opportunity_id=record.id,
            payload={"message": message, "title": record.title, "change_type": NEW},
        )
        print(f"[{marker}] new posting -> {path}")

    # --- simulate a changed deadline -----------------------------------
    with session_scope() as session:
        record = session.scalar(
            select(Opportunity).where(
                Opportunity.external_id == "demo-new-posting"
            )
        )
        old_deadline = record.deadline
        session.add(
            OpportunityVersion(
                opportunity_id=record.id,
                content_hash="demo-hash-1",
                title=record.title,
                description=record.description,
                eligibility=record.eligibility,
                deadline=old_deadline,
                skills=[],
            )
        )
        record.deadline = now + dt.timedelta(days=11)
        record.content_hash = "demo-hash-2"
        session.flush()

        changes = {
            "deadline": {"old": old_deadline, "new": record.deadline},
        }
        document = build_document(record, CHANGED, changes=changes, now=now)
        path = write_document(document, record.company_name, record.title, now=now)
        message = format_telegram(
            record, CHANGED, changes=changes, document_path=path, now=now
        )
        enqueue_alert(
            session,
            channel="telegram",
            change_type=CHANGED,
            dedup_key=f"demo:{record.id}:changed",
            opportunity_id=record.id,
            payload={"message": message, "title": record.title,
                     "change_type": CHANGED},
        )
        print(f"[{marker}] changed posting -> {path}")
        print(f"[{marker}] status tag used: "
              f"{'APPLIED' if args.status == 'APPLIED' else 'NEW'}")

    if args.send:
        from app.services.alerts import deliver_pending

        with session_scope() as session:
            results = deliver_pending(session)
        sent = sum(1 for r in results if r.ok)
        print(f"[{marker}] delivered {sent}/{len(results)} alert(s) to Telegram.")
    else:
        print(f"\nQueued {2} alerts. Re-run with --send to deliver them.")
    return 0


def _cmd_tools(args: argparse.Namespace) -> int:
    """List the tools the agent can call."""
    import json

    from app.agent.tools import tool_specs

    specs = tool_specs()
    if args.json:
        print(json.dumps(specs, indent=2))
        return 0
    for spec in specs:
        print(f"{spec['name']:<20} {spec['description']}")
    return 0


def _cmd_tool(args: argparse.Namespace) -> int:
    """Call one agent tool with JSON arguments.

    Example::

        python -m app.main tool parse_jd --arg opportunity_id=12
        python -m app.main tool web_search --args '{"company": "Zensar"}'
    """
    import json

    from app.agent.tools import ToolContext, call_tool

    if args.args:
        try:
            arguments = json.loads(args.args)
        except json.JSONDecodeError as exc:
            print(f"Invalid --args JSON: {exc}")
            return 1
        if not isinstance(arguments, dict):
            print("--args must be a JSON object.")
            return 1
    else:
        arguments = {}

    for item in args.arg or []:
        if "=" not in item:
            print(f"Invalid --arg {item!r}; expected KEY=VALUE.")
            return 1
        key, value = item.split("=", 1)
        try:
            arguments[key] = json.loads(value)
        except json.JSONDecodeError:
            arguments[key] = value

    with session_scope() as session:
        result = call_tool(args.name, arguments, ToolContext(session=session))
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


def _cmd_agent_run(args: argparse.Namespace) -> int:
    """Run the agent graph for one stored opportunity and print its brief."""
    import json

    from app.agent.runner import analyze_opportunity
    from app.models import Opportunity

    with session_scope() as session:
        opportunity = session.get(Opportunity, args.opportunity_id)
        if opportunity is None:
            print(f"No opportunity with id {args.opportunity_id}.")
            return 1
        result = analyze_opportunity(
            session, opportunity, args.change, should_save=not args.no_save
        )
    if args.json and result.state is not None:
        print(json.dumps(result.state.to_dict(), indent=2, default=str))
    else:
        print(result.markdown or "Dropped by the router: nothing to brief.")
    return 0


def _cmd_install_timers(_args: argparse.Namespace) -> int:
    """Print the commands to install the systemd user timers."""
    project = Path(__file__).resolve().parent.parent
    units = ("placement-poll", "placement-deliver", "placement-remind", "placement-digest")
    print("Run these to automate everything:\n")
    print("  mkdir -p ~/.config/systemd/user")
    for unit in units:
        print(
            f"  cp {project / 'deploy' / (unit + '.service')} "
            f"{project / 'deploy' / (unit + '.timer')} ~/.config/systemd/user/"
        )
    print(f"""
  systemctl --user daemon-reload
  systemctl --user enable --now {' '.join(u + '.timer' for u in units)}
  systemctl --user list-timers | grep placement

Schedule:
  placement-poll      every 30 min   collect new/changed postings
  placement-remind    every 15 min   deadline reminders
  placement-deliver   every 5 min    send queued alerts
  placement-digest    every 1 hour   daily digest (sends once per day)

Check logs with:
  journalctl --user -u placement-poll.service -n 50 --no-pager

Keep running after logout:
  sudo loginctl enable-linger $USER""")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Create database tables").set_defaults(func=_cmd_init_db)
    sub.add_parser("login", help="Interactive Superset login").set_defaults(func=_cmd_login)
    sub.add_parser("poll", help="Run one collection cycle").set_defaults(func=_cmd_poll)
    sub.add_parser("deliver", help="Drain the alert queue").set_defaults(func=_cmd_deliver)
    dryrun = sub.add_parser("dryrun", help="Collect and print without saving to DB")
    dryrun.add_argument("--scrolls", type=int, default=10,
                        help="Number of lazy-load scrolls to attempt")
    dryrun.add_argument("--status", default="",
                        help="Application status filter, e.g. 'Yet To Apply'")
    dryrun.add_argument("--tab", default="",
                        choices=["", "all", "applied"],
                        help="Job list tab to select")
    dryrun.add_argument("--search", default="",
                        help="Search box text, e.g. a company name")
    dryrun.add_argument("--detail-limit", type=int, default=0,
                        help="Max job-detail API calls (0 = no limit)")
    dryrun.add_argument("--verbose", action="store_true", help="Debug logging")
    dryrun.set_defaults(func=_cmd_dryrun)
    sub.add_parser("heartbeat", help="Check poller health").set_defaults(func=_cmd_heartbeat)
    sub.add_parser("stats", help="Show recent poll runs").set_defaults(func=_cmd_stats)
    sub.add_parser("telegram-chat-id",
                   help="Find your Telegram chat id").set_defaults(func=_cmd_telegram_chat_id)
    sub.add_parser("install-timers",
                   help="Print systemd user timer install steps").set_defaults(func=_cmd_install_timers)
    sub.add_parser("migrate",
                   help="Add missing columns to an existing database").set_defaults(func=_cmd_migrate)

    # --- agent tools (week 1) ------------------------------------------
    tools = sub.add_parser("tools", help="List the agent tools and their schemas")
    tools.add_argument("--json", action="store_true", help="Print full JSON schemas")
    tools.set_defaults(func=_cmd_tools)

    tool = sub.add_parser("tool", help="Call one agent tool")
    tool.add_argument("name", help="Tool name, e.g. parse_jd or web_search")
    tool.add_argument("--args", default="", help="JSON object of arguments")
    tool.add_argument(
        "--arg",
        action="append",
        metavar="KEY=VALUE",
        help="Single argument; repeatable. Values are parsed as JSON when possible.",
    )
    tool.set_defaults(func=_cmd_tool)

    # --- agent graph (week 2) ------------------------------------------
    agent = sub.add_parser("agent", help="Run the agent graph")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    agent_run = agent_sub.add_parser("run", help="Brief one stored opportunity")
    agent_run.add_argument("opportunity_id", type=int)
    agent_run.add_argument("--change", choices=("new", "changed"), default="new")
    agent_run.add_argument("--no-save", action="store_true", help="Do not store the brief")
    agent_run.add_argument("--json", action="store_true", help="Print the full state")
    agent_run.set_defaults(func=_cmd_agent_run)

    # --- testing --------------------------------------------------------
    demo = sub.add_parser(
        "demo",
        help="Test the agent -> document -> Telegram path with a fake posting",
    )
    demo.add_argument("--send", action="store_true",
                      help="Also deliver the queued alerts to Telegram")
    demo.add_argument("--status", default="YET_TO_APPLY",
                      choices=["YET_TO_APPLY", "APPLIED", "OFFERED", "REJECTED"],
                      help="Portal application status to simulate")
    demo.add_argument("--cleanup", action="store_true",
                      help="Remove demo records and the research/ folder")
    demo.set_defaults(func=_cmd_demo)

    # --- preferences ---------------------------------------------------
    prefs = sub.add_parser("prefs", help="Manage interests and alert thresholds")
    prefs_sub = prefs.add_subparsers(dest="action", required=True)
    prefs_sub.add_parser("show", help="Show current preferences").set_defaults(func=_cmd_prefs)

    prefs_set = prefs_sub.add_parser("set", help="Update preferences")
    prefs_set.add_argument("--skills", help='e.g. "python sql react"')
    prefs_set.add_argument("--interests", help='e.g. "backend data engineering"')
    prefs_set.add_argument("--technologies", help='e.g. "django postgres docker"')
    prefs_set.add_argument("--work-types", dest="work_types", help='e.g. "full time internship"')
    prefs_set.add_argument("--locations", help='e.g. "bangalore remote"')
    prefs_set.add_argument("--companies", help="Companies you always want alerts for")
    prefs_set.add_argument("--min-compensation", dest="min_compensation", type=float)
    prefs_set.add_argument("--degree")
    prefs_set.add_argument("--cgpa", type=float)
    prefs_set.add_argument("--graduation-year", dest="graduation_year", type=int)
    prefs_set.add_argument("--min-alert-score", dest="min_alert_score", type=float,
                           help="Only alert on jobs scoring at or above this (0-100)")
    prefs_set.add_argument("--always-alert-changes", dest="always_alert_changes",
                           action="store_true", default=None,
                           help="Always alert when a tracked job changes")
    prefs_set.add_argument("--reminder-days", dest="reminder_days",
                           help='Deadline reminder lead times, e.g. "3 1"')
    prefs_set.add_argument("--daily-digest", dest="daily_digest", action="store_true",
                           default=None, help="Enable the daily digest")
    prefs_set.add_argument("--digest-hour", dest="digest_hour", type=int)
    prefs_set.add_argument("--clear-matching", dest="clear_matching", action="store_true")
    prefs_set.add_argument("--clear-notifications", dest="clear_notifications",
                           action="store_true")
    prefs_set.set_defaults(func=_cmd_prefs)

    # --- application tracker -------------------------------------------
    track_p = sub.add_parser("track", help="Track applications")
    track_sub = track_p.add_subparsers(dest="action", required=True)
    track_sub.add_parser("list", help="List tracked applications").set_defaults(func=_cmd_track)

    track_set = track_sub.add_parser("set", help="Create/update an application")
    from app.services.applications import STATUSES

    track_set.add_argument("query", nargs="?", help="Job title or company substring")
    track_set.add_argument("--id", type=int, help="Opportunity id")
    track_set.add_argument("--status", default="saved", choices=list(STATUSES))
    track_set.add_argument("--priority", type=int)
    track_set.add_argument("--next-action", dest="next_action")
    track_set.add_argument("--on", help="Next action date, YYYY-MM-DD")
    track_set.add_argument("--notes")
    track_set.add_argument("--mark-applied", dest="mark_applied", action="store_true")
    track_set.set_defaults(func=_cmd_track)

    # --- reminders / digest --------------------------------------------
    sub.add_parser("remind", help="Queue deadline reminders").set_defaults(func=_cmd_remind)
    digest = sub.add_parser("digest", help="Queue the daily digest")
    digest.add_argument("--force", action="store_true", help="Send even if already sent today")
    digest.set_defaults(func=_cmd_digest)

    # --- AI ------------------------------------------------------------
    research = sub.add_parser("research", help="Generate a cited company report")
    research.add_argument("company")
    research.add_argument("--role", default=None, help="Target role for this research")
    research.add_argument("--force", action="store_true",
                          help="Re-research even if a report is already stored")
    research.set_defaults(func=_cmd_research)

    resume = sub.add_parser("resume", help="Suggest evidence-backed resume bullets")
    resume.add_argument("--id", type=int, help="Opportunity id")
    resume.add_argument("--query", help="Job title or company substring")
    resume.add_argument("--no-model", action="store_true",
                        help="Skip the LLM and return placeholders only")
    resume.set_defaults(func=_cmd_resume)
    review = sub.add_parser(
        "resume-review", help="Review and approve pending resume suggestions"
    )
    review.add_argument(
        "--approve", type=int, action="append", help="Suggestion id to approve"
    )
    review.set_defaults(func=_cmd_resume_review)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
