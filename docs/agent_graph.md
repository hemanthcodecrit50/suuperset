# Agent graph (week 3)

A bounded state machine in `app/agent/`. It turns a new or changed posting into a
verified brief. It never applies and never writes to the portal.

```
route ─ drop ──────────────────────────────► end
      ├ short ─ match ─ writer ─ approval ───► end
      └ full ─ plan ─ [researcher ‖ matcher ‖ resume advisor] ─ critic
                                                   │ >30% unsupported, first time
                                                   └─► researcher (strict) ─ critic
                                                   ▼
                                       drop unsupported
                                                   ▼
                                       writer ─ approval gate ─► end
```

| Node | File | What it does |
| --- | --- | --- |
| Router | `router.py` | Deterministic. New posting: full path. Eligible-no or student already in the process: short path. Past deadline, out of the process, or no tracked field changed: dropped |
| Planner | `planner.py` | Picks lookups from the parsed JD: bond clause, missing pay, missing rounds, unfamiliar company. At most 5 |
| Researcher | `researcher.py` | Runs the lookups in parallel through the `web_search` tool, discards results about other entities, builds claims. With a model, claims are synthesised from numbered evidence and the URL is taken from the evidence row, never from the model |
| Matcher | `matcher.py` | `score_fit` tool: hard eligibility plus fit score. Unknown stays unknown |
| Resume advisor | `resume_advisor.py` | `rank_portfolio` tool: projects to lead with, honest gaps, optional grounded bullets. Planned projects are never used |
| Critic | `critic.py` | Checks each claim against its cited source: numbers must appear exactly, 60% of key words must appear. If the snippet fails it fetches the page (max 3 per brief). Also checks resume bullets against the project record |
| Writer | `writer.py` | Produces a polished report from verified claims, with a deterministic fallback |
| Approval gate | `approval.py` | Stores grounded resume bullets as `approved=false` for student review |
| Memory | `memory.py`, `store.py` | Preferences, portfolio and cached company evidence (7 days) |

## Retry rule

If more than `AGENT_MAX_UNSUPPORTED_RATIO` of the claims fail, the researcher runs
once more with wider searches and quotes the evidence directly instead of
paraphrasing. After that the critic removes whatever is still unsupported and the
brief lists the topic under **Unknown**.

## Running it

```bash
python -m app.main agent run 12            # brief opportunity 12 and store it
python -m app.main agent run 12 --no-save  # print only
python -m app.main agent run 12 --json     # full state, trace and dropped claims
```

The poll job calls the graph for every alert-worthy NEW or CHANGED posting and
appends the brief to the `research/<company>/...md` document. It is skipped on the
seeding run, capped at `AGENT_MAX_BRIEFS_PER_RUN`, and a failure is logged without
stopping collection. A brief is stored in `research_reports` (`kind=agent_brief`)
and reused while the posting's content hash is unchanged.

Telegram delivery includes the same report returned by the writer. Review
pending bullets with `python -m app.main resume-review`.
