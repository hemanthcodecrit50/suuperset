# Verified Superset portal structure (Oct 2026)

Source: OCR + layout analysis of `sample_images/*.png` (home_page, filters,
job_profile, sort_by). Everything below was *visible in the screenshots*.
Items marked (?) are ambiguous from OCR and still need live confirmation.

## Domain / routes

| Thing | Value | Confidence |
| --- | --- | --- |
| Base domain | `https://app.joinsuperset.com` | high |
| Home / announcements feed | `GET /students` | high |
| Job profiles list | `GET /students/jobprofiles` | high |
| Selected-job param in URL | `?currentJobId=<uuid>` (OCR shows `currentjobld`) | medium (?) |
| Login URL | not captured — visiting `/students` logged-out presumably SSO-redirects | unverified |

## Global shell

- Top bar: site logo, search icon, notifications bell with count (`99+`), and a
  `School` selector (dropdown), plus other action icons.
- Left sidebar: `Home`, `Job Profiles`, `Interviews`, `Assessments`, `Events`,
  `Competitions`, `Resume`, `Launchpad`, `Help`.

## Home feed announcements

Each card contains:

```
<title line>                                   e.g. "Open for applications - Dover Corp's Job Profile - ..."
<poster> · <relative time>                     e.g. "Training and Placement BMSCE · 2 days ago"
<body paragraphs>
Applicable Courses
  1. M.Tech. - Digital Communication - ...
Eligibility
See More                                       (expand link)
[New badge on unread cards]
```

- `New` badge exists on at least some cards.
- Opened card shows `comment` action.
- Right rail: `Job Profiles` mini-panel and `Latest Blogs`.

## Job profiles list (`/students/jobprofiles`)

- Tabs: `All Jobs` | `Applied Jobs` (Query param or internal state: unknown).
- Filter row: `Job Sector` | `Position Type` | `Status` | `Sort by`, plus
  `Clear all filters`.
- `Status` options: `All`, `Applied`, `Yet To Apply`, `Not Interested`,
  `Withdrawn`, `Rejected`, `Offered`, `Offer Accepted`, `Offer Rejected`.
- `Sort by` options: `Created At`, `Job Profile Title`, `Company Name`,
  `Location`, `Status`, `Application Deadline`.
- Search box placeholder: `Search by job title or company`.

### Card fields

```
<title>                              e.g. "Application Developer - Associate"
<Company> | <WorkType> | <Location>  e.g. "Thoughtworks | Full Time | PAN India"
<Company> · <Location>               e.g. "Thoughtworks · PAN India"
<status badge>                       Applied / Applications closed / Yet to apply / ...
<relative time>                      e.g. "3 days ago"
Closes in <N> days                   e.g. "Closes in 13 days"
[if applied] "You applied with resume : <name>.", "O You applied with resume : ..."
```

- Statuses observed on cards: `Applied`, `Applications closed`, `Yet to apply`
  (and filter lists the full set above).

## Detail panel (right side, same page — SPA)

Opening a card updates the URL param and renders:

```
Opening Overview
  Category: <tier>                    e.g. "Tier 2"
  Role
  Job Functions: <fn>                e.g. "Engineering"
  Job Profile CTC: <₹>               e.g. "1110000 per Annum"
  Other Info:
Job Description                       (long text, with "Show More")
Hiring Workflow                       (rounds/steps)
Eligibility Criteria                  (degree/CGPA/branches)
Additional Questions
Required skills / "No skills added for this job profile"
```

## JSON API (primary collection path) ✅

Discovered from the portal's own network traffic. This is **far more reliable
than DOM scraping** and is now the default collector mode.

Base: `https://app.joinsuperset.com/tnpsuite-core`

| Endpoint | Returns |
| --- | --- |
| `GET /session` | Current user (`userUUID`, roles) |
| `GET /students/{userUUID}/job_profiles` | **All ~237 job profiles**, no pagination |
| `GET /students/{userUUID}/job_profiles/{id}` | Full detail: JD, CTC, eligibility |
| `GET /user-notifications?channel=WEB&page=N` | Notifications; 50/page, 0-indexed, empty page ends it |

### Required headers

```
authorization: Custom <key>       # derived at runtime from the _sc cookie
x-requester-client: webapp
x-superset-tenant-id: <account code>
x-superset-tenant-type: STUDENT
```

**No token is hardcoded.** `app/collector/api.py` captures these headers from
the page's own outgoing requests at runtime, falling back to deriving the
authorization value from the `_sc` cookie saved by `scripts/login.py`.

### Useful payload fields

*Job list item:* `jobProfileIdentifier` (stable id), `jobProfileTitle`,
`companyName`, `jobProfileLocation`, `positionTypeLabel`,
`placementCategoryName`, `jobProfileApplicationDeadline` (epoch ms),
`jobProfileStatus`, `jobApplicationStatus`, `createdAt`.

*Job detail:* `jobProfile.jobDescription` (HTML), `jobProfile.ctcMin/ctcMax/
ctcInterval/ctcCurrency`, `jobProfile.eligibilities`, `applicableCourses`,
`eligibilityCheckResult.result` (your personal eligibility verdict).

*Notification:* `uuid` (stable id), `content` (HTML), `object`
(`JOBPROFILE` / `STUDENT_NOTICE`), `action`, `urlParamMap.job_profile_id` /
`notice_uuid`, `createdAt` (epoch ms → absolute date).

## DOM structure (fallback only)

The original DOM-scraping path is retained as a fallback but is **no longer the
primary collector** — the portal exposes a JSON API (see below).

Resolved by the Selenium script in this folder (verified):

- Login URL: `https://app.joinsuperset.com/students`
- Logged-in test: `driver.title == 'Home'` (app titles: `Home`, `Job Profile`)
- Login form: email input, password input, `Login` button. The original element
  ids (`«r5»`, `«r6»`, `«rl»`) are React-generated and **unstable**, so
  type/text-based selectors are used instead.
- Job Profiles nav button: `id="demo_step_3"` (sidebar uses `demo_step_N`).
- Status filter select: `id="mui-component-select-jobApplicationStatus"`
  (MUI naming convention). Menu options are `li` elements inside a listbox.
- Status options: `All, Applied, Yet To Apply, Not Interested, Withdrawn,
  Rejected, Offered, Offer Accepted, Offer Rejected`
- Framework: **Material UI**.

## How this maps to the code

- `app/collector/api.py` — auth discovery + typed endpoint calls.
- `app/collector/mappers.py` — API JSON → `RawOpportunity` / `RawAnnouncement`.
- `app/collector/notifications.py`, `job_profiles.py` — DOM parsing, kept as a
  fallback (`COLLECTOR_MODE=dom`).
- `app/selectors.py` — DOM selectors (only used in fallback mode).
- `app/jobs/poll.py` — orchestrates API-first collection, with DOM fallback.
- `python -m app.main dryrun --detail-limit 3 --verbose` to validate.