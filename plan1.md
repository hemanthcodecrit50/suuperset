### Implementation blueprint: Superset Placement Intelligence

The main architectural decision is this: run the collector on a free, always-on cloud worker only if that provider can run a persistent browser process; keep the database, dashboard and notification services independently replaceable.

For a realistic zero-cost setup, I'd begin with a free-tier Linux VM if you can obtain one, plus a free PostgreSQL service. A serverless deployment can host the dashboard and API, but isn't automatically suitable for persistent Playwright polling. Free tiers change, so treat the options below as candidates rather than guarantees of perpetual free service.

### 1\. Hosting choices

![Oracle Cloud Infrastructure (OCI) : Create a Compute VM - ORACLE-BASE](https://images.openai.com/static-rsc-4/CZNzoWTU6MFMiN_jWJCOPfhcMXhhM6ubPyKkJAyYna6_3u-OFZZ7jb2N9iuHGhm6TEdM6urIHnmMKvGXqq5CVJEOpcbj_EAwO-kKNwAKMU3cJCpj1WPyS5DIZGtK_QyaBI8nC894bpaGQYT0nY2V2dLUZASgoYaKcwNelaU62jfWbWN9r30OOf3Wyt8vgQ70?purpose=fullsize)

Option A — Free VM, best fit if available

A Linux VM runs Python, Playwright, the scheduler and possibly the dashboard in one place.

* No separate worker scheduler needed.

* Good for browser automation and a long-running process.

* Availability, resource limits and free-tier eligibility can vary.

![Getting Started with GitHub Actions: A Comprehensive Guide for Beginners | Asier Ortiz - Software Developer](https://images.openai.com/static-rsc-4/Le83n5ZY9Ay1LdyaXIcMfWoqo-J93fjNxdL7gJafdhCEuMF7532YvQBELoIKtNTmZmmh5n38fCqlJmtKOh_YBC6pITrTrLgTS5Ae3-JYfMI1WVXwEkOYDBuGS5G1odzzUH1B6Z3z09NcURJ7gUiOK11KsBW4NM4K9p05IG8at-EclNJBdjKq5jXmrMk__Lwr?purpose=fullsize)

Option B — Scheduled GitHub Actions

Run a short collector workflow on a schedule, with a hosted database and dashboard elsewhere.

* No always-on VM to maintain.

* Scheduled runs can be delayed and aren't guaranteed to run at an exact time.

* Check the current Actions usage limits before relying on it for frequent monitoring.

* Persistent authenticated browser sessions and SSO reauthentication need careful handling.

![Announcing Neon Serverless Postgres as an Azure Native Integration (Preview) | Microsoft Community Hub](https://images.openai.com/static-rsc-4/ajnR39Rde2vWXDNpma8B7tCZ3CLYCYXmi0mlaj99_5zBr759w17orlXKTLIOipB0TtCuFVNQJS0W4k6ZJiGubDv8bw_WP2rCkV8GckfpLT-kbvL7lulllhunVFwbpnVpt_oE5BKgD5M-Rz6dL691_7Fvd6hvstUdJFvvMFqbZygYTR-NzNPLJsibCAotGk4D?purpose=fullsize)

Database — managed PostgreSQL

Use a managed service such as Neon for the initial database, subject to its current free-tier limits.

Neon announced a 1 GB-per-project free-plan storage limit on October 2, 2026. Verify compute, inactivity and connection limits before deploying.

My preference: Option A for collection, managed PostgreSQL for storage, and Streamlit for the initial dashboard. If you can't obtain a suitable free VM, use scheduled Actions as a fallback and accept less predictable polling.

### 7\. Company research and resume intelligence

Use a two-stage AI pipeline: first gather evidence, then generate an analysis. Don't ask an LLM to produce a company report from memory alone.

### Research pipeline

New or changed job

Search and collect evidence

Official company pages, careers site, engineering blogs, annual reports and reputable news

Extract structured facts

Company, products, responsibilities, requirements, compensation, dates and source URLs

Generate the report

Role assessment, risks, interview preparation and next actions

For a quality-first but free-first implementation:

* Use a search engine or permitted search API for discovery.

* Prefer official sources for compensation, eligibility and company claims.

* Use reputable independent sources for employee experiences and business risks.

* Use free API quotas where available, with a local model as a fallback.

* Cache reports and regenerate only when the job changes or research becomes stale.

A practical local option is Ollama. See [its official website](https://ollama.com/). A small VM may not have enough memory to run a useful model comfortably, so local inference on your own computer may be preferable.

Do not scrape search-engine result pages aggressively or bypass website restrictions. Use sources and APIs according to their terms.

### Resume matching rules

Your resume engine should score opportunities against verified data, not just keywords.

A simple initial scoring model might be:

| Criterion                             | Weight |
| ------------------------------------- | ------ |
| Skills and project alignment          | 35%    |
| Role and career-interest alignment    | 25%    |
| Compensation and location preferences | 20%    |
| Preferred technologies and work type  | 20%    |

These weights should be configurable. Eligibility requirements such as degree, CGPA or graduation year should be evaluated separately as hard constraints. If information is missing, mark it unknown instead of assuming eligibility.

For each proposed bullet, store:

* The original verified project achievement.

* The job requirement it matches.

* The technologies genuinely used.

* A link or evidence supporting the claim.

* Whether you approved the wording.

This lets you build a reusable resume intelligence system instead of repeatedly asking an AI to reconstruct your experience.

### 8\. Polling schedule and reliability

Start with polling every 30 minutes, assuming the portal permits that frequency. Use a longer interval if the portal is sensitive to repeated access.

For a Linux VM, a `systemd` timer is a simple option. Once `app/jobs/poll.py` is implemented and tested, create `/etc/systemd/system/placement-poll.service`:

```ini
[Unit]
Description=Superset Placement Poller

[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu/placement-intelligence
EnvironmentFile=/home/ubuntu/placement-intelligence/.env
ExecStart=/home/ubuntu/placement-intelligence/.venv/bin/python -m app.jobs.poll
```

Create `/etc/systemd/system/placement-poll.timer`:

```ini
[Unit]
Description=Run placement poller every 30 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=30min
Persistent=true

[Install]
WantedBy=timers.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now placement-poll.timer

systemctl list-timers placement-poll.timer
journalctl -u placement-poll.service -n 100 --no-pager
```

Replace `ubuntu` and the paths if your VM uses a different account or directory.

This is only the scheduling configuration, not the collector implementation itself. Ensure the poll command exits cleanly, uses a database transaction, and doesn't overlap with a previous poll.

For reliability, also implement:

* A lock to prevent overlapping runs.

* Retry with exponential backoff for transient network failures.

* A timeout on browser navigation.

* A login-expiry detector.

* Alert deduplication by opportunity, change type and channel.

* A daily heartbeat if no successful poll has occurred recently.

* Database backups and a tested restore procedure.

A free-tier deployment can still sleep, lose its VM, exceed its resource quota or change its limits. Zero-cost hosting is a target, not an uptime guarantee.

### 9\. Implementation roadmap

### 01

MVP

Portal collection and PostgreSQL

Get one authenticated page working, extract notifications and JDs, save them, and detect new versus changed records. Validate the results against what you see in the browser.

### 02

Alerts

Telegram, email and reminders

Implement a durable alert queue, retries, deadlines and a daily digest. Test duplicate announcements and modified JDs.

### 03

Dashboard

Applications and preferences

Build filters, saved jobs, status tracking, eligibility rules and system-health reporting.

### 04

AI

Research and resume recommendations

Add sourced company reports, fit analysis, interview preparation and evidence-backed resume bullets.

### 10\. What I need before writing the actual scraper

The architecture and schema are ready to use, but the Superset collector cannot be made accurate without knowing the specific portal's page structure. I don't want to invent endpoints or selectors that won't work for your college.

Please provide these two things:

* Your college's Superset login URL — just the URL, not your credentials, cookies or session tokens.

* A screenshot of the notifications page and a job-profile detail page after you log in. Redact your name, email, application identifiers and other personal information.

With those, the next implementation step is to map the actual pages, decide how pagination and attachments work, and build the collector against the real structure. We can then implement `poll.py`, the database upserts and Telegram alerts first, before moving on to AI research and the dashboard.

The most important milestone is not generating a polished report. It is getting a collector that reliably notices a new opening or a changed deadline, stores it once, and alerts you without missing updates or flooding you with duplicates.
