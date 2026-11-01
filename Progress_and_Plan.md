# Placement Copilot: Progress and Plan

Agentic AI course project, built on the Superset Campus Placement Intelligence System (`suuperset/`). Status as of 2026-11-01 — agent layer complete through week 3; end-to-end pipeline implemented and wired into the poll job.

## 1. The idea in brief

A student-side agent for Superset (`app.joinsuperset.com`). It watches the student's job profiles and home feed. For every new or changed item it produces a decision-ready brief that answers one question: **"Should I apply, and how do I prepare and present myself?"**

The student decides. The agent never applies and never writes to the portal.

## 2. Done so far

### 2.1 Working system: `suuperset/` (the base we build on)

Verified against the live portal: 183 announcements and 237 job profiles collected, deduplicated and stored.

| Area | What exists |
| --- | --- |
| Collector | Playwright SSO login (`scripts/login.py`), JSON API client as the primary path, DOM scraping as fallback, login-expiry detection |
| Change detection | Stable `external_id` plus content hash, version history kept, idempotent upserts. No LLM involved |
| Storage | 13-table PostgreSQL schema (`sql/schema.sql`), SQLite for tests |
| Alerts | Telegram and email queue with dedup, retries, failure logging, alert-flood cap, first run seeds without alerting |
| Scheduling | systemd user timers: poll (30 min), remind (15 min), deliver (5 min), daily digest. File lock prevents overlapping runs |
| Personal tracking | Application statuses, preferences, portfolio items with evidence links |
| Matching | Keyword-overlap fit score with fixed weights and hard eligibility rules |
| Research (thin) | One DuckDuckGo Instant Answer lookup plus one model call. Research agent writes `research/<company>/<date>-<role>.md` and a tagged Telegram message |
| Dashboard | Streamlit app: Overview, Opportunities, Applications, Announcements, Portfolio, Preferences, AI assistant, Alerts |
| AI setup | Any OpenAI-compatible endpoint. Local Ollama is the default. OpenCode free models can't be called from outside OpenCode |
| Tests | 14 pytest files (collector, mappers, parsers, matching, alerts, resume, research agent, resilience, dashboard, UI smoke) |
| Docs | `README.md`, `docs/portal_structure.md`, `docs/ai_setup.md`, `plan1.md` (hosting and blueprint), `plan2.md` (project brief) |

Telegram tags: `🆕 NEW`, `✅ APPLIED`, `🔄 UPDATED`, `⏰ DEADLINE REMINDER`, `🎉 OFFERED`, `❌ REJECTED`, `🔒 CLOSED`.

**Known gaps** (these are what the agent layer closes):
- No planning.
- No verification of claims.
- No JD-specific resume advice.
- Weak search backend.
- Keyword-only fit scoring.

### 2.2 Course deliverables produced

All files are in `AgenticAI/`.

| File | What it is |
| --- | --- |
| `context.md` | Project context summary |
| `Problem_Charter.pdf` | Full problem charter (niche, problem, decision, agentic concepts, feasibility) |
| `Problem_Charter_1page.pdf` | Charter compressed to 1 page |
| `Placement_Copilot.pptx` and `.pdf` | 8-slide, 10-minute deck with speaker notes (the PDF has no notes) |
| `Placement_Copilot_Architecture.pptx` and `.pdf` | Slides on architecture and agent graph, agentic patterns and rationale, data sources and tool plan, and evaluation plan. Next steps cut to 3 weeks |
| `Placement_Copilot_Design_Document.pdf` | 3-5 page design document covering the same points |
| `Team_QA.md` and `.pdf` | Plain-English Q&A so every team member can explain the design and code |
| `zip.zip` | Archive of the above |

Two-sentence problem statement (decided):
> Placement portals like Superset bury each student's job profiles and procedure alerts in a feed, and students have little time to read the JD, research the company or judge their fit before the deadline. They also send one generic resume to every company, because nothing assembles the portal data, the web research and their own profile into a decision-ready brief for each opportunity.

### 2.3 Week 1 agent layer (done)

The tool layer the week-2 graph will call. Every tool has a strict JSON schema
and returns a recoverable `{ok, tool, result|error}` envelope.

| File | What it does |
| --- | --- |
| `app/agent/tools.py` | 9-tool registry: `get_opportunity`, `parse_jd`, `web_search`, `fetch_page`, `score_fit`, `rank_portfolio`, `send_alert`, `research_company`, `get_student_profile` |
| `app/services/jd_parser.py` | Deterministic JD parser: skills, rounds, bond, pay, eligibility, degree, CGPA, batch, location, work type, deadline. Unknown stays null |
| `app/services/pages.py` | Allow-listed page fetch for claim verification; SSRF checks, redirect re-validation, byte/char caps |
| `app/services/search.py` | Composite backend: Wikipedia/Wikidata official site + GDELT news + optional Brave + DuckDuckGo fallback; 7-day cache, GDELT throttle |
| `app/services/cache.py` | Atomic JSON file cache with TTL |
| `docs/agent_tools.md` | Tool contracts, configuration, safety rules |
| `tests/test_jd_parser.py`, `test_pages.py`, `test_search_backends.py`, `test_tools.py` | 54 new tests; full suite passes |

Verified live: `web_search` returned the Wikipedia summary and the Wikidata
official website for Zensar, and `fetch_page` extracted text from the cited
Wikipedia page. CLI: `python -m app.main tools` and
`python -m app.main tool <name> --args '{...}'`.

Nothing here writes to the portal. `parse_jd` and `score_fit` only persist with
`persist=true`; `send_alert` only queues an alert for the existing worker.

## 3. Plan decided earlier

### 3.1 Architecture

```
Scheduler -> Collector -> PostgreSQL -> Agent graph -> Delivery -> Student
 (built)     (built)       (built)      (NEW)          (built)     (decides)
```

Only new or changed hashes reach the agent graph. The graph reads student memory (preferences, portfolio evidence, cached company briefs), calls tools, and writes a report back to the database. Delivery reuses the existing Telegram renderer and report files.

### 3.2 Agent graph

A bounded state machine, not an open-ended loop. All nodes share one state: the opportunity, the student profile, evidence items (each with a source URL), the draft report, the critic's verdict and a retry count.

1. **Router** classifies the change. A new JD takes the full path. A procedure update for an applied company takes the short path to the writer. Noise is dropped.
2. **Planner** reads the parsed JD and picks the tasks. A bond clause, missing pay or an unfamiliar company each trigger different lookups.
3. **Specialists, in parallel:**
   - **Researcher** gathers sourced evidence.
   - **Matcher** checks hard eligibility and computes the fit score.
   - **Resume advisor** maps JD requirements to real portfolio items and lists honest gaps.
4. **Critic** checks every claim against its cited source and drops unsupported ones. If too many fail, it goes back to the researcher once. After that retry, remaining unsupported claims are removed and the report says what is unknown.
5. **Writer** produces the Critical Information Report and the alert.
6. **Student** approves any resume edit.

### 3.3 Agentic patterns and why

| Pattern | Why | Status |
| --- | --- | --- |
| RAG, evidence first | A model must not describe a company from memory | Extends existing two-stage pipeline |
| Routing | Most feed items are noise or small updates | New |
| Planning | JDs differ, so a fixed script wastes calls or misses risks | New |
| Multi-agent roles | Different tools and prompts, parallel to meet the 10-minute target | Wraps existing services |
| Reflection (critic) | Targets hallucination by checking claims against sources | New |
| Human in the loop | The student decides. Resume edits are reword and reorder only | Partly built |

### 3.4 Data sources and tools

| Source | Trust |
| --- | --- |
| Superset JSON API | Authoritative |
| Student profile and portfolio | Authoritative |
| Official company pages (careers, product, engineering blog, annual reports) | High |
| Reputable news (funding, layoffs, controversies) | Medium |
| Interview experiences from public forums | Low, labelled anecdotal |

| Tool | Purpose | Status |
| --- | --- | --- |
| `get_opportunity` | Load JD, versions and diff | Built; wrapped as an agent tool |
| `parse_jd` | Extract skills, rounds, bond and pay. Unknown stays null | Built (week 1) |
| `web_search` | Find official and news sources. Rate-limited, cached 7 days | Upgraded (week 1): Wikipedia/Wikidata + GDELT + optional Brave + DDG fallback |
| `fetch_page` | Pull text from a cited URL for verification. Allow-list, size cap | Built (week 1) |
| `score_fit` | Eligibility rules and weighted fit score | Built; wrapped as an agent tool |
| `rank_portfolio` | Match JD requirements to real projects | Built; wrapped as an agent tool |
| `send_alert` | Telegram message from the same facts. Deduplicated, capped | Built; wrapped as an agent tool |

### 3.5 Report contents

- Deadline, eligibility, fit score
- Company snapshot with cited sources
- Likely rounds and prep topics
- Risk flags (bond, pay, location, negative news)
- Resume advice: missing JD keywords, projects to reorder, bullets to rewrite, honest gaps (never invented experience)

### 3.6 Delivery plan (3 weeks)

| Week | Deliverable |
| --- | --- |
| 1 | Existing services wrapped as tools. JD parser and page-fetch tool. Better search backend |
| 2 | Agent graph: router, planner, parallel researcher, matcher and resume advisor. Critic with one retry. Wired into the poll job |
| 3 | Writer and report output, resume approval gate, Telegram message from the report, run on real alerts |

Focus: a system that works end to end on real portal data. Evaluation work (golden set, metrics, ablations, LLM judge) is dropped.

### 3.7 Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Scraping policy | Read-only, student's own session, placement cell permission sought. Fallback is manual JD paste |
| Hallucination | Evidence-first retrieval, critic, citations, confidence labels, unknown stays unknown |
| Fabricated resume claims | Reword and reorder only, approval required, zero-tolerance gate |
| Privacy | Local storage, secrets out of version control, consent for any shared data |
| Cost and rate limits | Routing skips noise, briefs cached, retry capped at one, local model by default |

**Open assumption:** the student has institute permission to read Superset with their own login. If not, the system switches to manual JD paste.

## 4. Status against the plan

| Item | Status |
| --- | --- |
| Collector, DB, alerts, scheduler, dashboard | Done |
| Problem charter, deck, design doc, Team Q&A | Done |
| Tool registry and JSON schemas (`app/agent/tools.py`) | Done (week 1) |
| `parse_jd` and `fetch_page` tools | Done (week 1) |
| Search backend upgrade | Done (week 1) |
| Agent graph: router, planner, parallel researcher / matcher / resume advisor, critic with one retry | Done (week 2) |
| Graph wired into the poll job (brief appended to the research document) | Done (week 2) |
| Writer node and Telegram message built from the report | Done (week 3) |
| Resume approval gate | Done (week 3) |

Week 3 is complete: see `docs/agent_graph.md`. Week 1 is complete: nine tools return a recoverable `{ok, tool, result|error}`
envelope, `parse_jd` is deterministic, `fetch_page` is allow-listed and
size-capped, and search is cached for 7 days with a free/keyless-first backend.
All 54 new tests pass, and the CLI can invoke any tool (`python -m app.main
tools`, `python -m app.main tool <name>`). Weeks 2 and 3 added the rest of the
agent pipeline — router -> planner -> parallel specialists -> critic (one retry)
-> writer -> approval gate — and wired it into the poll job via
`python -m app.main agent run <id>`.

Dropped: golden set, evaluation metrics and harness, ablations, LLM judge, pilot rating.

## 5. Immediate next step

Week 3 is done and the pipeline now runs end to end — see `docs/agent_graph.md`;
the shared graph state, router, planner, specialist and critic steps originally
listed here are implemented and covered by tests.

The immediate next step is now **evaluation and pilot feedback**, not further
implementation of the graph.

Next steps to be done:

1. Run the full agent pipeline on live alerts and capture briefs end to end.
2. Measure report grounding: run briefs with `python -m app.main agent run <id> --json`
   and check the critic's unsupported-claim rate and the ≥90% claim-traceability target.
3. Measure approval usefulness: use `python -m app.main resume-review` and collect
   student feedback on the resume advice and pending approvals.
4. Measure delivery reliability: verify Telegram/email brief delivery on real alerts,
   including dedup and the alert-flood cap.
5. Pilot on live alerts and collect qualitative feedback; adjust planner task
   selection and critic thresholds from the findings.
