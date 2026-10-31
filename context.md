# Project Context: Placement Copilot Agent

## Summary
A personal agent for students using Superset (app.join.superset.com), the platform institutions use to manage campus placements. It watches the student's profile and home feed, and for every new job profile or procedure alert it researches the company and produces a Critical Information Report plus JD-specific resume advice.

## Problem (two sentences)
Placement portals like Superset bury each student's job profiles and procedure alerts in a feed, and students have little time to read the JD, research the company or judge their fit before the deadline. They also send one generic resume to every company, because nothing assembles the portal data, the web research and their own profile into a decision-ready brief for each opportunity.

## Niche
Student-side assistant layer on top of a campus placement portal. The portal is a system of record for the institute and gives no advice to the individual student.

## Decision supported
"Should I apply, and how do I prepare and present myself?"
- Apply or skip: eligibility check, fit score, risk flags (bond, pay, location, negative news)
- How to prepare: company brief, likely rounds, what changed since the last alert
- How to present: JD-specific resume edits and honest gaps

The student stays the decision maker. The agent never applies or writes to the portal.

## Pipeline
1. Scrape profile and home feed on a schedule (Playwright, student's own session).
2. Hash each item; only new or changed hashes trigger the agent.
3. Classify: new JD (full pipeline), procedure update for an applied company (short path), or noise.
4. Research: JD parsing, web search and fetch, student profile and resume from the database.
5. Critic step verifies claims against sources and drops unsupported ones.
6. Generate the Critical Information Report and resume suggestions; notify the student.

## Agentic concepts
Change detection and triggers, autonomy, planning and task decomposition, tool use, retrieval-augmented memory (student DB, cached company briefs), multi-agent roles (Classifier, Researcher, Matcher, Resume Advisor, Report Writer), reflection and verification, human in the loop.

## Report contents
- Deadline, eligibility, fit score
- Company snapshot with cited sources
- Likely rounds and prep topics
- Risk flags
- Resume optimisation: missing JD keywords, projects to reorder, bullets to rewrite, honest gaps (never invented experience)

## Feasibility
- Stack (proposed): Python, Playwright, SQLite or Postgres, LLM with tool calling, search API, LangGraph or a plain tool-calling loop.
- Milestones: week 1 scraper and hashing; week 2 JD parser and DB; week 3 research agent; week 4 resume advisor and critic; week 5 alerts, evaluation on 10 to 15 past JDs, demo.
- Risks: scraping policy (get placement cell permission, own session, read-only, fall back to manual JD paste); hallucination (citations, critic, confidence labels); privacy (local storage, consent); fabricated resume claims (reword and reorder only).
- Success metrics: alert to report under 10 minutes; at least 90% of claims traceable to a source; resume keyword match improves after edits; pilot usefulness rating of 4/5 or better.

## Open assumption
The student has institute permission to scrape Superset with their own login. If not, switch to manual JD paste.

## Deliverables so far
| File | Description |
| --- | --- |
| Problem_Charter.pdf | Problem Charter (also a Claude Doc titled "Problem Charter: Placement Copilot Agent") |
| Placement_Copilot.pptx | 8-slide deck for a 10-minute talk, with speaker notes |
| Placement_Copilot.pdf | PDF of the deck (no speaker notes) |
