# Agent tools (week 1)

The week-1 deliverable is the tool layer the week-2 graph will call. Each tool
has a JSON schema, a single handler, and a stable result shape. The registry is
in [`app/agent/tools.py`](../app/agent/tools.py).

```bash
python -m app.main tools                 # names + descriptions
python -m app.main tools --json          # full JSON schemas
python -m app.main tool parse_jd --args '{"text": "Required skills: Python, SQL"}'
python -m app.main tool web_search --arg company=Zensar
python -m app.main tool get_opportunity --arg opportunity_id=12
```

Every call returns an envelope, so a model can read a failure instead of
crashing the graph:

```json
{"ok": true,  "tool": "parse_jd", "result": { ... }}
{"ok": false, "tool": "parse_jd", "error": "Provide jd text or an opportunity_id with a stored JD."}
```

## Tools

| Tool | What it does | Writes? |
| --- | --- | --- |
| `get_opportunity` | Load one posting by id or title/company, with recent versions and a field diff. | No |
| `parse_jd` | Extract skills, rounds, bond, pay, eligibility, location and deadline. Unknown fields stay `null`. | Only with `persist` |
| `web_search` | Search Wikipedia/Wikidata, GDELT news and optional Brave; DuckDuckGo is the keyless fallback. | No |
| `fetch_page` | Fetch a cited URL as text, after allow-list and SSRF checks. | No |
| `score_fit` | Eligibility rules plus the weighted fit score from stored preferences. | Only with `persist` |
| `rank_portfolio` | Rank verified projects against JD requirements; list possible gaps. | No |
| `send_alert` | Queue a deduplicated Telegram/email/dashboard alert. | Queue only |
| `research_company` | Generate or reuse a cited company/role brief. | Research cache |
| `get_student_profile` | Preferences, notification settings and verified portfolio items. | No |

The agent never writes to Superset and never publishes a resume edit. `send_alert`
queues; the existing delivery worker sends.

## `parse_jd`

Deterministic, no model. It reads labels and sections from the JD (including
HTML), then returns:

```json
{
  "skills": ["Python", "SQL", "Docker"],
  "rounds": ["Online Assessment", "Technical Interview"],
  "bond": {"applicable": true, "months": 12, "amount": 100000, "currency": "INR"},
  "pay": {"amount": 12, "currency": "INR", "unit": "lakh", "interval": "annum"},
  "eligibility": "B.Tech, minimum CGPA 7.0",
  "degree": "B.Tech",
  "min_cgpa": 7.0,
  "graduation_year": 2027,
  "deadline": "2026-11-30",
  "unknown_fields": ["location", "work_type", "experience"]
}
```

Rules:

- No value is guessed. A missing deadline, bond or pay figure is `null` and is
  listed in `unknown_fields`.
- "No bond" is reported as `bond.applicable = false`, not as a missing bond.
- Skills combine explicit "Required skills" lines with a bounded technology
  vocabulary found in the prose.
- Rounds prefer a "Hiring Workflow"/"Interview Process" section, then fall back
  to lines that name a round, test, assessment or interview.
- `parse_jd` can take `opportunity_id` to use the stored JD; add `persist` to
  save the result under `raw.parsed_jd`.

## `web_search`

Backends, in trust order:

1. **Wikipedia + Wikidata** — company overview, description and the official
   website (`P856`). Source type `reference` or `official`.
2. **GDELT DOC 2.0** — recent news. Reputable outlets are preferred; other
   outlets are kept as `news_low`. Source type `news`.
3. **Brave Search** — only when `BRAVE_SEARCH_API_KEY` is set. Source type `web`.
4. **DuckDuckGo Instant Answer** — keyless fallback. Source type `reference`/`web`.

All results carry `title`, `claim`, `source_url`, `source_type`, `published_at`
and `excerpt`. Results are cached for 7 days (`SEARCH_CACHE_TTL_HOURS`), and
GDELT is throttled to one request every 5 seconds. Wikimedia asks for a
descriptive user agent; set `SEARCH_USER_AGENT` to your own contact or repo URL
before deploying.

Configuration:

```bash
SEARCH_BACKEND=auto          # auto | composite | wikipedia | gdelt | brave | duckduckgo
SEARCH_CACHE_DIR=.cache/search
SEARCH_CACHE_TTL_HOURS=168
SEARCH_TIMEOUT_SECONDS=20
SEARCH_MAX_RESULTS=5
SEARCH_GDELT_TIMESPAN=2y
SEARCH_USER_AGENT=placement-intelligence/0.1 (contact: you@example.com)
BRAVE_SEARCH_API_KEY=
```

## `fetch_page`

This is the verification tool: the critic fetches the page behind a claim and
checks the cited text. Safety rules:

- `http`/`https` only; no credentials in the URL.
- Host must match `FETCH_PAGE_ALLOWED_DOMAINS` or a per-call `allow_domains`.
- Private, loopback, link-local and reserved addresses are rejected (SSRF).
- Every redirect is re-validated before it is followed.
- Responses are capped (`FETCH_PAGE_MAX_BYTES`), extracted to text, and never
  rendered or executed.
- Pages are cached for 7 days. The cache is checked only after the allow-list,
  so a cached page never bypasses the rules.

To verify a company's own site, pass it explicitly:

```bash
python -m app.main tool fetch_page \
  --args '{"url": "https://www.zensar.com/about", "allow_domains": ["zensar.com"]}'
```

## Adding a tool

1. Write a handler `(ToolContext, dict) -> dict` that returns JSON-safe data.
2. Raise `ToolError` with a message a model can act on.
3. Register a `Tool` in `TOOLS` with a strict JSON schema.
4. Add a test in `tests/test_tools.py`.

Week 2's graph should call `call_tool(...)` rather than importing handlers
directly, so every step stays observable and failures stay recoverable.
