# Superset Campus Placement Intelligence System

A zero-cost-first, modular monolith that monitors Superset placement
notifications and job profiles, stores them in PostgreSQL, detects new and
changed records, alerts on Telegram/email, and (later) generates sourced
company research and evidence-backed resume suggestions.

> **Status: working against the live portal.** The portal exposes a JSON API,
> so collection is API-first (fast, structured, stable IDs) with DOM scraping
> kept as a fallback. Verified end-to-end: 183 announcements and 237 job
> profiles collected, deduplicated, and persisted.

## Features

- **Collector** — authenticated Playwright session + the portal's read-only JSON API.
- **Change detection** — stable `external_id`s plus content hashes; historical versions preserved.
- **Alert queue** — Telegram + email, deduplicated, retried, with failure logging.
- **Personal tracking** — application statuses, preferences, deadlines.
- **Research** — two-stage evidence-first company/role reports (structure + caching).
- **Resume intelligence** — JD ↔ verified portfolio matching, approval required.
- **Agent tools** — a schema-backed tool layer for the week-2 graph, including a
  deterministic JD parser, allow-listed page fetch and a composite search backend.
- **Dashboard** — Streamlit skeleton for opportunities, deadlines and health.

## Repository layout

```text
app/
├── main.py                     # CLI entry point
├── config.py                   # pydantic-settings configuration
├── db.py                       # engine + session_scope()
├── models.py                   # SQLAlchemy ORM models
├── dashboard.py                # Streamlit dashboard
├── selectors.py                  # central DOM selector registry (fallback mode)
├── collector/
│   ├── browser.py                # Playwright session + login-expiry detection
│   ├── api.py                    # JSON API client (primary collection path)
│   ├── mappers.py                # API JSON -> collector dataclasses
│   ├── notifications.py          # announcement parser (DOM fallback)
│   ├── job_profiles.py           # job JD parser (DOM fallback)
│   └── change_detector.py        # hashes, diffing, idempotent upserts
├── services/
│   ├── alerts.py               # Telegram/email delivery + retries
│   ├── matching.py             # relevance scoring + hard eligibility rules
│   ├── research.py             # sourced company reports
│   ├── resume.py               # evidence-backed resume suggestions
│   ├── search.py               # composite search: Wikipedia/Wikidata, GDELT, Brave, DDG
│   ├── jd_parser.py            # deterministic JD extraction (skills, rounds, bond, pay)
│   ├── pages.py                # allow-listed, size-capped page fetch for verification
│   └── cache.py                # 7-day file cache for search results and pages
└── jobs/
    └── poll.py                 # lock, retries, persistence, heartbeat
agent/
├── research_agent.py          # document.md + tagged Telegram message
└── tools.py                   # tool registry + JSON schemas (week 1)
sql/schema.sql                  # PostgreSQL schema
scripts/login.py                # interactive SSO login bootstrap
deploy/                         # systemd service + timer templates
tests/                          # pytest suite (SQLite, no external services)
```

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium

cp .env.example .env        # then edit DATABASE_URL and portal URLs
chmod 600 .env
```

Create the schema and run the CLI:

```bash
python -m app.main init-db     # dev/test convenience (or: psql < sql/schema.sql)
python -m app.main login       # interactive SSO login -> secrets/storage_state.json
python -m app.main poll        # one collection cycle
python -m app.main deliver     # drain the alert queue
python -m app.main heartbeat   # alert if polling is unhealthy
python -m app.main stats       # recent poll-run health
```

Validate extraction against the live portal without touching the DB:

```bash
python -m app.main dryrun --scrolls 10   # prints collected JSON
```

### Verify locally without PostgreSQL

PostgreSQL is optional for a first run — SQLite works for verification because
the ORM models use a SQLite-safe primary-key variant.

```bash
# 1. Environment
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium        # one-off browser download (~150 MB)

# 2. Config
cp .env.example .env
sed -i 's|^DATABASE_URL=.*|DATABASE_URL=sqlite:///placement.db|' .env
chmod 600 .env
python -c "from app.config import get_settings; print(get_settings().database_url)"

# 3. Schema
python -m app.main init-db

# 4. Sanity check, no browser or DB needed
pytest

# 5. Authenticate once (headed browser: log in by hand, then it closes)
python -m app.main login

# 6. Confirm collection works, scoped to a small query
python -m app.main dryrun --detail-limit 3 --verbose

# 7. Persist a real cycle and check health
python -m app.main poll
python -m app.main stats
```

Useful flags:

| Flag / variable | Purpose |
| --- | --- |
| `--detail-limit N` | Cap job-detail API calls (cheap dry-runs) |
| `--status "Yet To Apply"` | Filter to one application status |
| `--verbose` | Debug logging |
| `COLLECTOR_MODE` | `auto` (default), `api`, or `dom` |
| `FETCH_JOB_DETAILS=false` | Skip per-job JD/eligibility fetches |
| `MAX_ALERTS_PER_RUN=25` | Alert flood cap |
| `SUPPRESS_ALERTS_ON_FIRST_RUN=true` | First poll seeds the DB without alerting |

The first poll stores the whole backlog but sends **no alerts** (see
`SUPPRESS_ALERTS_ON_FIRST_RUN`), so you are not flooded with 400 notifications
on day one. Subsequent polls only alert on genuinely new or changed records.

Dashboard:

```bash
streamlit run app/dashboard.py
```

Tests:

```bash
pytest
```

## The web app (main way to use everything)

```bash
python -m app.main web     # http://127.0.0.1:8000 (this computer only)
```

| Page | What you do there |
| --- | --- |
| **Inbox** | New and changed postings with fit, deadline, status and "Brief ready" chips. Filter and search |
| **Posting** | The agent brief with cited sources, the JD, your status, and a **Run agent** button |
| **Approvals** | Approve or reject suggested resume bullets; nothing is used until you approve it |
| **Applications** | Everything you track, soonest deadline first |
| **Profile & system** | Skills, CGPA and portfolio; **Connect Superset**; **Run poll now**; recent polls |

**Superset login without storing credentials.** **Connect Superset** opens the
real Superset page in a browser window on your computer. You log in there
yourself; the app keeps only the session cookies (`secrets/storage_state.json`,
gitignored, readable only by you). Your password is never seen or stored.
**Disconnect** deletes the session.

Everything is free: FastAPI, htmx (bundled in `app/web/static`, works offline),
SQLite or local PostgreSQL, free search APIs, and optional local Ollama for the
model features (resume bullets, report polishing). See
[`docs/ai_setup.md`](docs/ai_setup.md).

Telegram and email still work but are optional and off unless configured. The
older Streamlit dashboard (`streamlit run app/dashboard.py`) is kept for now.

## Agent tools (week 1)

The week-2 graph will run on top of a schema-backed tool layer. Existing
services are wrapped, not rewritten.

```bash
python -m app.main tools                 # list tools
python -m app.main tools --json          # JSON schemas for function calling
python -m app.main tool parse_jd --args '{"text": "Required skills: Python, SQL"}'
python -m app.main tool parse_jd --arg opportunity_id=12 --arg persist=true
python -m app.main tool web_search --arg company=Zensar
python -m app.main tool fetch_page --args '{"url": "https://en.wikipedia.org/wiki/Zensar_Technologies"}'
```

| Tool | Wraps |
| --- | --- |
| `get_opportunity` | posting + version history |
| `parse_jd` | deterministic JD parser |
| `web_search` | Wikipedia/Wikidata + GDELT + optional Brave + DuckDuckGo |
| `fetch_page` | allow-listed, size-capped page text for claim verification |
| `score_fit` | eligibility + weighted relevance score |
| `rank_portfolio` | JD ↔ verified project ranking |
| `send_alert` | deduplicated notification queue |
| `research_company` | cited company/role brief |
| `get_student_profile` | preferences + portfolio memory |

Search results and fetched pages are cached for 7 days. `fetch_page` rejects
private addresses, re-checks redirects, and only fetches allow-listed hosts.
Full contracts and configuration: [`docs/agent_tools.md`](docs/agent_tools.md).

## What it does now

The narrow loop, end to end:

1. **Poll** the portal every 30 min (JSON API, hash-based change detection — no LLM)
2. **Detect** a new or changed posting
3. **Research agent** writes `research/<company>/<date>-<role>.md` with the
   critical facts plus one AI-written summary line
4. **Telegram** gets a neat tagged message built from the *same* facts
5. **Deadline reminders** reuse the same renderer

### Tags

`🆕 NEW` · `✅ APPLIED` · `🔄 UPDATED` · `⏰ DEADLINE REMINDER` ·
`🎉 OFFERED` · `❌ REJECTED` · `🔒 CLOSED`

An `APPLIED` posting is tagged APPLIED even when it first appears, because that
is the more useful fact.

### Example message

```
🆕 NEW · *Backend Engineer Intern*
🏢 Zensar Technologies
📍 Internship · Pune, Bangalore
💰 Effective 125,000 INR \(month\)
🎓 Eligible
⏰ Closes in *6* day(s) · 2026-10-20
https://app.joinsuperset.com/students/jobprofiles?currentJobId=...
📄 research/zensar-technologies/2026-10-14-backend-engineer-intern.md
```

Change detection never uses the LLM — only the one-line summary does, and it
falls back to a factual line when no model is reachable. Unknown deadlines and
eligibility are written as `unknown`, never guessed.

## Scheduling (automatic operation)

`deploy/` holds systemd units with your real paths already filled in:

- `placement-poll.{service,timer}` — collect every 30 min
- `placement-remind.{service,timer}` — deadline reminders every 15 min
- `placement-deliver.{service,timer}` — send queued alerts every 5 min
- `placement-digest.{service,timer}` — daily digest (hourly timer, sends once)

Install them as **user** units (no sudo needed):

```bash
python -m app.main install-timers   # prints the exact commands
```

That runs:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/placement-poll.service deploy/placement-poll.timer ~/.config/systemd/user/
cp deploy/placement-deliver.service deploy/placement-deliver.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now placement-poll.timer placement-deliver.timer
systemctl --user list-timers | grep placement
```

Logs:

```bash
journalctl --user -u placement-poll.service -n 100 --no-pager
journalctl --user -u placement-deliver.service -n 100 --no-pager
```

To keep timers running after you log out:

```bash
sudo loginctl enable-linger $USER
```

The poll command takes a non-blocking file lock, so an overrunning run is
skipped rather than started twice.

## Turning on Telegram alerts

```bash
# 1) Message @BotFather in Telegram -> /newbot -> copy the token
# 2) Open your bot and press Start
# 3) Find your chat id:
python -m app.main telegram-chat-id
```

Add both to `.env`:

```bash
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=987654321
```

Test the pipeline end-to-end:

```bash
python -m app.main heartbeat    # queues a "collector unhealthy" alert
python -m app.main deliver      # sends everything pending
```

## Scheduling (Linux VM)

## Verifying the portal

The collectors in `app/collector/*.py` were written from the screenshots in
`sample_images/` (see `docs/portal_structure.md` for the verified structure).
Rows are located by probing candidate selectors and all values are parsed from
rendered text, so exact DOM classes do not need to be known up front.

Remaining unknowns that need a live look (documented in `docs/portal_structure.md`):

- The login page URL and SSO redirect flow (no screenshot was provided).
- Exact DOM classes / whether the feed and list lazy-load on scroll.
- The `currentJobId` query-param casing.

Process once you can log in:

1. `python -m app.main login` — complete SSO manually; state is saved.
2. `python -m app.main dryrun --scrolls 10` — inspect the extracted JSON and
   compare against the browser.
3. Adjust selector probes / parsers in `notifications.py` and `job_profiles.py`
   if anything mismatches, then confirm `poll` upserts look right.

### Supplying exact identifiers

All portal selectors live in one file: `app/selectors.py`. The verified
identifiers (login URL, `#demo_step_3` nav button, MUI status filter,
`Home`/`Job Profile` titles) are already filled in. To override or extend them
without editing code, copy `docs/selectors.example.json`, fill it in and set:

```bash
SUPERSET_SELECTORS_FILE=selectors.json
```

Anything you don't specify keeps the text-based heuristics. If you'd rather not
copy selectors by hand, paste the `outerHTML` of one announcement card and one
job detail panel and we'll derive the selectors from it.

## Security

- Do not bypass authentication, CAPTCHA, MFA or access controls.
- Read only data you are authorized to access.
- Never commit `.env`, `secrets/`, cookies or browser storage state.
- Storage state is written with owner-only permissions by `scripts/login.py`.

## Design notes

- **Unknown stays unknown.** Missing deadlines, eligibility or compensation are
  stored as `NULL` and reported as `unknown`, never guessed.
- **Idempotency.** Unique `(source_id, external_id)` constraints plus content
  hashes mean a re-poll never creates duplicates or duplicate alerts.
- **Independently replaceable.** Collector, database, alerts and dashboard can
  be deployed separately. Free-tier availability is a target, not a guarantee.
