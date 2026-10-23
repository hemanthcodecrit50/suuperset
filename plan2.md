Here’s a self-contained summary you can copy into another ChatGPT conversation to continue the project.

# Project Brief: Superset Campus Placement Intelligence System

## 1. Project Goal

Build a **zero-cost, or as close to ₹0 as possible, campus placement intelligence system** for Superset, the academic and recruitment platform used by my college.

The system should monitor placement notifications and job openings, alert me based on my interests and application status, research new companies and roles, summarize opportunities, and recommend truthful, job-specific resume improvements based on my existing projects and skills.

Priorities:
- Free-tier cloud hosting with 24/7 monitoring if feasible.
- Authenticated access through the normal Superset website and institutional SSO.
- Telegram, email, and dashboard notifications.
- High-quality company and job research, prioritizing free sources and APIs.
- PostgreSQL as the central database.
- A practical implementation with minimal unnecessary complexity.

## 2. Required Features

### A. Superset Notification Monitoring
- Monitor new placement announcements, job profiles, JDs, eligibility criteria, deadlines, assessment schedules, and interview updates.
- Detect both new announcements and meaningful changes to existing records.
- Handle JavaScript-rendered pages, scrolling, pagination, and linked job-detail pages where applicable.
- Avoid duplicate records and duplicate notifications.
- Preserve historical versions of JDs and eligibility/deadline changes.
- Detect expired sessions and alert me when manual reauthentication is required.

### B. Personalized Opportunity Tracking
- Maintain a personal application tracker with statuses such as saved, eligible, applied, assessment, interview, offer, rejected, withdrawn, and not eligible.
- Filter and prioritize opportunities by job function, company, location, compensation, technology stack, eligibility, and personal career preferences.
- Track deadlines and upcoming actions.
- Separate confirmed facts from unknown or unverified information.
- Maintain notification preferences and configurable relevance scores.

### C. Company and Job Research
When a new or materially changed opportunity is discovered, generate a structured report containing:
- Company overview, products, customers, business model, and industry.
- Recent developments, launches, funding, and other relevant news where verifiable.
- Engineering culture and technology stack, if reliable evidence exists.
- Job responsibilities, requirements, expected skills, and likely interview topics.
- Compensation and internship-to-full-time conversion details when available.
- Career growth, potential concerns, and red flags.
- Source links, publication dates, and evidence for important claims.
- A concise recommendation and actionable next steps.

Prioritize official company sources, careers pages, engineering blogs, annual reports, and credible independent reporting. Never invent information or present unsupported claims as facts.

### D. Resume and Project Intelligence
Maintain a structured database of my existing projects, skills, technologies, achievements, and supporting evidence.

For each job:
- Compare its JD with my verified project and skills database.
- Identify the most relevant projects and technical achievements.
- Generate tailored resume bullet-point suggestions.
- Map each suggestion to actual evidence and the corresponding JD requirement.
- Identify skill gaps and interview preparation priorities.
- Track whether project features are planned, in progress, implemented, or completed.

Never fabricate experience, metrics, technologies, or achievements. Resume suggestions must remain evidence-based and require my approval before use.

### E. Notifications
Support:
- Telegram bot messages.
- Email notifications.
- A personal dashboard.
- Urgent deadline reminders and a daily digest.
- Notification deduplication, retries, and failure logging.

Each notification should ideally include the company, role, what changed, deadline, why the opportunity matters to me, a source link, and a recommended next action.

## 3. Proposed Architecture

Use a modular Python application:

1. **Collector:** Python + Playwright for authenticated browser automation.
2. **Scheduler:** Linux systemd timer or another appropriate scheduler.
3. **Database:** PostgreSQL.
4. **Change detector:** Stable identifiers, content hashes, and historical versions.
5. **Alert worker:** Telegram, email, retries, and deduplication.
6. **Research service:** Web search and source collection.
7. **AI analysis service:** JD extraction, company reports, relevance scoring, and resume recommendations.
8. **Dashboard:** Streamlit initially.
9. **API:** FastAPI only if needed for additional integrations.

Start with a modular monolith, not microservices or a complicated multi-agent architecture.

### Proposed Repository Structure

```text
placement-intelligence/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── db.py
│   ├── models.py
│   ├── collector/
│   │   ├── browser.py
│   │   ├── notifications.py
│   │   ├── job_profiles.py
│   │   └── change_detector.py
│   ├── services/
│   │   ├── alerts.py
│   │   ├── matching.py
│   │   ├── research.py
│   │   └── resume.py
│   └── jobs/
│       └── poll.py
├── sql/schema.sql
├── scripts/login.py
├── tests/
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## 4. PostgreSQL Data Model

The proposed schema contains:

- `sources`: portal/source configuration.
- `companies`: company metadata and research.
- `opportunities`: current job-opening details, eligibility, skills, compensation, deadlines, URLs, and hashes.
- `opportunity_versions`: historical descriptions and changes.
- `announcements`: placement notifications and their content hashes.
- `applications`: my personal application statuses and next actions.
- `user_preferences`: interests and notification preferences.
- `portfolio_items`: projects, verified skills, achievements, and evidence.
- `research_reports`: structured company/job research with source references.
- `resume_suggestions`: proposed resume bullets linked to projects and opportunities.
- `alert_deliveries`: notification queue, deduplication, retries, and delivery status.
- `poll_runs`: polling history, errors, item counts, and system health.

Use PostgreSQL transactions and idempotent upserts. Unknown deadlines or eligibility information must remain unknown rather than being guessed.

## 5. Hosting and Zero-Cost Strategy

Preferred approach:
- A free-tier Linux VM for persistent browser automation, scheduling, and potentially the dashboard.
- Managed PostgreSQL, such as Neon, if its current free-tier limits fit the workload.
- Telegram Bot API for free notifications.
- SMTP or an email provider with a suitable free allowance.
- Local LLM inference using Ollama when practical.
- Free online search/API quotas for current company research, with caching and rate limits.

Fallback:
- Scheduled GitHub Actions for collection if a suitable free VM is unavailable, accepting that polling may be delayed and is not guaranteed to run exactly on schedule.

Important: free-tier availability, resource quotas, inactivity policies, and service limits can change. A genuine 24/7 zero-cost deployment is not guaranteed. Keep the collector, database, and dashboard independently deployable.

## 6. Authenticated Scraping and Security

The portal is authenticated and JavaScript-driven. Its exact page structure and API availability have not yet been verified.

Use Playwright to:
- Open the normal portal login page.
- Allow me to complete institutional SSO/MFA manually.
- Save authenticated browser state securely if permitted.
- Read only pages and data I am authorized to access.
- Detect session expiration and request manual reauthentication.
- Parse visible notifications, job profiles, links, and attachments.

Do not:
- Bypass authentication, CAPTCHA, MFA, or access controls.
- Assume Superset has a universal public student API.
- Invent endpoints or selectors without examining the actual portal.
- Commit credentials, cookies, session state, or `.env` files to Git.
- Log secrets or unnecessarily collect other students' personal information.

Inspect the real portal first. If a documented or otherwise authorized read-only endpoint exists, evaluate it as a potentially more stable alternative to DOM scraping.

Start with gentle polling, initially around every 30 minutes if the portal's rules permit it. Use timeouts, retries with exponential backoff, overlap prevention, logging, backups, and a heartbeat alert.

## 7. Suggested Implementation Phases

### Phase 1 — Reliable Collector
- Inspect the actual Superset portal.
- Implement login/session handling.
- Extract announcements and job profiles.
- Normalize data and save it to PostgreSQL.
- Detect new and changed content.
- Preserve historical versions.

### Phase 2 — Notifications
- Configure Telegram and email.
- Implement an idempotent notification queue.
- Add deadline reminders, retries, and a daily digest.

### Phase 3 — Personal Dashboard
- Display new opportunities and urgent deadlines.
- Add filters and eligibility information.
- Implement application tracking and personal preferences.
- Show collector health and authentication failures.

### Phase 4 — Company Research
- Gather current, cited evidence.
- Generate structured company and role reports.
- Cache research and refresh it when appropriate.
- Separate verified facts from opinions and uncertain information.

### Phase 5 — Resume Intelligence
- Populate the verified project/skills database.
- Implement JD-to-project matching.
- Generate evidence-backed resume bullets.
- Add approval and edit workflows.

### Phase 6 — Deployment and Reliability
- Deploy to a suitable free-tier environment.
- Configure scheduled polling and secure secrets.
- Add monitoring, backups, recovery, and tests.
- Validate deadline changes, session expiry, duplicate handling, and notification delivery.

## 8. Initial Commands and Dependencies

Target: Ubuntu Linux, Python 3.11+, PostgreSQL.

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip \
  git curl ca-certificates postgresql-client

mkdir -p ~/placement-intelligence
cd ~/placement-intelligence

python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install playwright streamlit "psycopg[binary,pool]" \
  sqlalchemy pydantic-settings python-dotenv \
  httpx beautifulsoup4 trafilatura \
  apscheduler tenacity openai pytest

playwright install chromium
playwright install-deps chromium
```

The intended `.env` configuration includes the database URL, real Superset URLs, Telegram credentials, SMTP settings, polling interval, and timezone. Use placeholders in `.env.example`, restrict `.env` permissions, and keep secrets out of source control.

The database schema and initial Playwright login-bootstrap approach have already been outlined in the previous conversation. Implement the actual collector only after verifying the real portal structure.

## 9. Immediate Next Step

Before writing a working Superset scraper, obtain:

1. The college's actual Superset login URL (URL only; never request credentials).
2. A redacted screenshot of the notifications page.
3. A redacted screenshot of a job-profile detail page.
4. Details of any pagination, scrolling, linked PDFs, or attachments.

Use these to determine the actual page structure and implement accurate extraction logic.

**First milestone:** reliably detect a new opening or changed deadline, store it once in PostgreSQL, and send one correct notification without duplicates. Add AI research and resume generation only after this foundation works.

Please continue from this brief with practical, step-by-step implementation instructions, exact file contents and commands, secure defaults, and free-first tool choices. Do not assume portal-specific details that have not been verified.
