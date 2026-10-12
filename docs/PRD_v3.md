# Product Requirements Document — v3
# Industry Radar: from a local tool to a hosted web app

**Version:** 3.0
**Status:** Architecture agreed, ready to plan the build
**Author:** Sam
**Last Updated:** October 2026
**Builds on:** `docs/PRD.md` (v1) and `docs/PRD_v2.md` (v2). The pipeline, sources, briefing and Jobs logic from v2 stay; this doc changes where they run, how they're stored and how people reach them.

---

## 0. How to use this doc

Sections 1–3 say what v3 is and who it's for. Section 4 is the architecture. Section 5 records each major decision with the reasoning, the alternatives considered and the trade-offs. Section 6 is the new repo structure, section 7 the data model, section 8 the build phases.

Section 5 is written to be explainable out loud: each decision ends with a short "In one sentence" summary.

---

## 1. Goal

Today Industry Radar runs on my laptop: `python main.py` starts a Streamlit dashboard and a daily scheduler, and everything lives in one SQLite file. Only I can use it, and only while my laptop is on.

v3 turns it into an **always-on web app that other people can sign into**:
- Anyone in the Ross community signs in with Google and gets their own account.
- Each user picks the industries they follow and gets a daily research brief, an event tracker and company profiles for those industries.
- Each user has private Jobs data: resumes, job preferences, scored matches and tailored resume advice.
- The app runs on hosted infrastructure, so it works from any device at any time.

## 2. Users and launch

- **First audience:** the Michigan Ross community (MBA students), so a few hundred users.
- **What they care about:** industry knowledge for recruiting and interviews, plus finding and applying to roles. That's why Consulting and a Banking / PE / VC split join the industry catalog.
- **Ownership:** a personal project under my own accounts, not a club or employer.

## 3. Scope

**In scope**
- Next.js front end built from the approved redesign (Sign in, Choose industries, Briefing, Event tracker, Companies, Jobs).
- A Python API in front of the existing pipeline, Jobs and briefing code.
- Postgres database with per-user data, Google sign-in and row-level security.
- A separate worker for the daily data collection and brief writing.
- Usage limits and cost metering, error tracking, and delete/export of personal data.

**Out of scope for v3**
- Paid data sources (PitchBook, Crunchbase, Bloomberg).
- Scraping LinkedIn, Indeed, or any site whose terms forbid it.
- Paid plans and billing. The metering is built now so plans can be added later without a redesign.
- Native mobile apps (the web app should work on a phone browser).

---

## 4. Architecture

```
            Browser (desktop or phone)
                       │
                       ▼
        ┌──────────────────────────────┐
        │  Next.js front end (Vercel)  │  screens, Google sign-in UI
        └──────────────┬───────────────┘
                       │ HTTPS + user's sign-in token (JWT)
                       ▼
        ┌──────────────────────────────┐
        │  FastAPI backend (Render)    │  checks the token, enforces limits,
        │  wraps existing Python code  │  runs Jobs scoring & resume tailoring
        └──────────────┬───────────────┘
                       │
                       ▼
        ┌──────────────────────────────┐
        │  Supabase                    │  Postgres + row-level security,
        │                              │  Google auth, resume file storage
        └──────────────▲───────────────┘
                       │
        ┌──────────────┴───────────────┐
        │  Pipeline worker (Render     │  daily: collect events per followed
        │  cron job)                   │  industry, update companies, write briefs
        └──────────────────────────────┘
                       │
          Claude API · Tavily · openFDA · SEC · PubMed · arXiv · ClinicalTrials
```

**How a request flows:** the user signs in with Google through Supabase and receives a token. The Next.js app sends that token with every call to FastAPI. FastAPI checks it, looks up the user, checks their usage limits, and reads or writes Postgres. Row-level security in Postgres is the second lock: even if the API had a bug, one user's rows can't be read with another user's token.

**How data gets collected:** once a day the worker finds every sub-industry with at least one follower, collects and extracts events for it once, updates company data, and writes that industry's brief. Users never trigger collection, so a slow or failing source never slows down the website.

---

## 5. Decisions and reasoning

### 5.1 Leave Streamlit for a custom web app

**Decision:** replace the Streamlit dashboard with a custom front end.

**Why:**
- Streamlit reruns the whole script on every click. That caused real problems in v2: opening a job reran the page and scrolled to the top.
- The approved redesign needs things Streamlit makes hard: a sign-in flow, an onboarding screen for choosing industries, collapsible job descriptions, searchable checkbox dropdowns and a custom look.
- Streamlit is built for one person exploring data, not hundreds of signed-in users with private data.

**Alternatives considered:**
- *Stay on Streamlit and add `st.login`:* fastest path to multi-user, but keeps the rerun problems and limits the design.
- *Streamlit plus custom components:* adds complexity without fixing the core model.

**Trade-off:** more code to own (a front end and an API instead of one Python script). Worth it because the product is moving from a personal analysis tool to something other people use daily.

**In one sentence:** Streamlit was the right tool for a solo prototype, but its rerun-everything model and limited UI control stopped fitting once the product needed sign-in, private data and a designed experience.

### 5.2 Next.js for the front end

**Decision:** Next.js (React, TypeScript), styled with Tailwind CSS and shadcn/ui components, hosted on Vercel.

**Why:**
- It's the most common React framework, so documentation, examples and hiring familiarity are strong.
- Server-side rendering makes the brief and event pages load fast and look complete on first paint.
- Supabase has first-class Next.js support for Google sign-in.
- Tailwind + shadcn/ui gives ready-made accessible components (dropdowns, dialogs, tables) that can be restyled to the redesign's teal/amber look.
- Vercel deploys on every push to GitHub, with preview links for each branch.

**Alternatives considered:**
- *Plain React (Vite):* fine, but I'd assemble routing and server rendering myself.
- *SvelteKit / Vue:* good frameworks, smaller ecosystem.

**Trade-off:** the front end is in TypeScript while the backend stays in Python, so there are two languages. Accepted because each is the strongest choice for its side.

**In one sentence:** Next.js gave the biggest ecosystem, fast-loading pages and built-in Supabase auth support, and Vercel made deployment a non-issue.

### 5.3 FastAPI as the backend, keeping the existing Python

**Decision:** put a FastAPI service in front of the existing `pipeline/`, `sources/` and `jobs/` code instead of rewriting it.

**Why:**
- All the valuable logic is already in Python and tested (125 tests): source collectors, Claude extraction, fit scoring, resume tailoring, brief writing. Rewriting it in TypeScript would add risk and no user value.
- FastAPI is Python, fast, and generates API docs automatically from type hints. The code already uses Pydantic models, which FastAPI uses natively.
- A separate API keeps secrets (Anthropic, Tavily keys) on the server, never in the browser.
- Long Claude calls (tailoring takes ~45 seconds) belong in a backend that can run them without browser timeouts.

**Alternatives considered:**
- *Next.js API routes only:* would mean porting all Python logic to TypeScript.
- *Django:* more built in than needed; FastAPI is lighter and fits an API-only service.
- *Front end talks straight to Supabase:* fine for simple reads, but usage limits and Claude calls need a trusted server.

**Trade-off:** one more service to deploy and monitor. Accepted to reuse proven code and keep secrets server-side.

**In one sentence:** I kept the tested Python core and put a thin FastAPI layer in front of it, so I changed how the app is delivered without rewriting the parts that already worked.

### 5.4 Postgres on Supabase for the database

**Decision:** move from SQLite to Postgres hosted on Supabase, and use Supabase for Google sign-in and resume file storage.

**Why:**
- **SQLite can't be shared.** It's a single file on one machine; a hosted API and a separate worker both need to reach the same database.
- **The data is relational.** Users follow industries, industries have events, events mention companies, users watch companies. Joins and foreign keys fit this better than a document store.
- **Row-level security (RLS).** Postgres can enforce "a user only sees their own resumes" inside the database, not just in app code. Resumes are personal data, so I want two locks.
- **Auth and storage included.** Supabase bundles Google sign-in (and can restrict it to university emails) and file storage, so there are fewer vendors.
- **Low migration effort.** The SQLite schema is already relational SQL, so most tables move over with small changes plus a `user_id`.
- **Cost:** free tier for development; the Pro plan (~$25/month) covers a few hundred users.

**Alternatives considered:**
- *Firebase / MongoDB:* document stores; relationships between events, companies and users get awkward, and no RLS in the same form.
- *Neon or plain Postgres on Render:* good Postgres, but auth and file storage would be separate services.
- *Keep SQLite on a persistent disk:* only one process can safely write, and it doesn't scale past one server.

**Trade-off:** some dependency on Supabase. Mitigated because it's standard Postgres underneath; the data and SQL move to any Postgres host if needed.

**In one sentence:** the data is relational and includes private resumes, so I chose Postgres for joins and row-level security, on Supabase because it bundles Google sign-in and storage and is still plain Postgres if I ever leave.

### 5.5 Collect industry data once, share it across users

**Decision:** events, briefs and company data are stored per industry and shared. Users only store their subscriptions, watchlist, resumes and job preferences.

**Why:**
- Collection and brief writing are the main fixed costs (Claude + search calls). If 200 students follow Consulting, the pipeline runs once, not 200 times.
- Pipelines run only for sub-industries with at least one follower, so unused industries cost nothing.
- A user's custom focus (e.g. "Surgical robotics") that fits an existing sub-industry becomes a cheap filter on that pipeline; only genuinely new areas get their own pipeline, and those are reviewed first.

**Trade-off:** briefs aren't personalized per user. Accepted for cost; personalization can be a filter layer later.

**In one sentence:** cost grows with the number of industries, not the number of users, because each industry's data is collected once and shared.

### 5.6 Separate pipeline worker from the web API

**Decision:** daily collection runs as its own scheduled job (a Render cron job), not inside the API process.

**Why:**
- A collection run makes many slow external calls. Inside the API it would compete with users' requests and could crash the site.
- The v2 app already hit a version of this: the scheduler could start twice when it lived in two processes.
- Separate processes can be scaled, restarted and debugged independently.

**Trade-off:** two deployments instead of one. Both use the same Python packages, so the code isn't duplicated.

**In one sentence:** background work and user-facing requests have different needs, so they run as separate processes that share the same code and database.

### 5.7 Usage limits and metering from day one

**Decision:** a usage table records every metered action and its Claude/search cost. Limits at launch: 1 "Find & score jobs" per user per day, plus a cap on resume tailoring (number to be decided).

**Why:**
- Jobs features are the main per-user cost (each tailoring is a long Claude call). Without limits, a handful of heavy users could set the monthly bill.
- Recording cost per action shows what each feature really costs, which drives pricing if paid plans come later.
- A `plans` table means limits are data, not hard-coded numbers.

**Trade-off:** some users will hit limits. Accepted; limits can be raised once real usage data exists.

**In one sentence:** I built cost metering before launch so I could set limits from real numbers and add paid plans later without redesigning anything.

### 5.8 Built to scale beyond Ross

**Decision:** a `communities` table (Ross first), `user_id` + RLS on every user-owned table, dev and prod environments with versioned migrations, spend caps and alerts on paid APIs, Sentry error tracking, and user data delete/export.

**Why:**
- Adding a second school should be a new row, not a code change.
- Migrations in git make every schema change reviewable and repeatable across dev and prod.
- Spend caps protect against a runaway bug burning through API credit.
- Delete/export respects that resumes and job searches are personal.

**In one sentence:** I launched small but made the expensive-to-change things (data ownership, environments, cost controls) right from the start.

### 5.9 Only trusted, verifiable sources (carried over from v2)

**Decision:** events come only from an allowlist of trusted domains plus primary sources (FDA, SEC, PubMed, arXiv, ClinicalTrials). Every event links to its source.

**Why:** users act on this for interviews and job decisions, so every claim must be checkable. In v2, filtering to trusted sources removed 413 of 519 events, which showed how much noise unfiltered web search added.

**In one sentence:** I traded volume for trust: fewer events, but every one links to a source you can verify.

---

## 6. New repo structure

The existing Python packages stay where they are. New top-level folders hold the API, the worker, the front end and the database migrations.

```
medrobo-radar/
├── web/                     NEW  Next.js front end (TypeScript, Tailwind, shadcn/ui)
│   ├── app/                      routes: sign-in, onboarding, briefing, events,
│   │                             companies, jobs
│   ├── components/               shared UI (tables, dropdowns, brief renderer)
│   └── lib/                      Supabase client, API client
├── api/                     NEW  FastAPI service
│   ├── main.py                   app setup, CORS, error tracking
│   ├── auth.py                   verifies the Supabase token, loads the user
│   ├── limits.py                 usage checks and metering
│   └── routes/                   briefs, events, companies, industries, jobs, account
├── worker/                  NEW  daily pipeline entry point (replaces the scheduler in main.py)
│   └── run_daily.py              runs every followed sub-industry, writes briefs
├── supabase/                NEW  database as code
│   ├── migrations/               versioned SQL: tables, indexes, RLS policies
│   └── seed.sql                  industry catalog, Ross community
├── pipeline/                KEPT search, extraction, synthesis, momentum, query agent
├── sources/                 KEPT openFDA, SEC Form D, research sources, trusted domains
├── jobs/                    KEPT job boards, collector, fit scoring, resume tailoring
├── profiles/                KEPT industry profile YAMLs (become the industry catalog seed)
├── database/                CHANGED  SQLite code replaced by a Postgres data layer
├── email_digest/            KEPT daily email, now sent per user's industries
├── tests/                   KEPT + new API and RLS tests
├── dashboard/               REMOVED after the new app replaces it
└── docs/                    PRDs, architecture notes
```

**Why this layout:** the Python core is shared by `api/` and `worker/` without copying code; the front end can be deployed on its own; and the database schema lives in git next to the code that uses it.

---

## 7. Data model

**Shared data (one copy per industry)**

| Table | Purpose |
|---|---|
| `communities` | Ross first; later other schools or groups |
| `industries` | Catalog: sectors and sub-industries, with an active flag |
| `events` | Collected events, each tied to an industry and a source link |
| `companies` | Company profiles, "what they do", momentum |
| `briefs` | Daily research brief per industry |
| `pipeline_runs` | Run history, errors and cost per run |
| `job_postings` | Postings found on public job boards (shared, de-duplicated) |

**Per-user data (`user_id` + row-level security)**

| Table | Purpose |
|---|---|
| `profiles` | User account details and community |
| `subscriptions` | Which industries the user follows, plus any custom focus |
| `watchlist` | Companies the user is watching |
| `resumes` | Uploaded resumes, mapped to role types (file in Supabase Storage) |
| `job_preferences` | Role types, locations, seniority, industries |
| `job_matches` | Scored matches for the user |
| `resume_advice` | Tailored resume changes per job |

**Cost control**

| Table | Purpose |
|---|---|
| `plans` | Limits per plan (launch: one free plan) |
| `usage_events` | Every metered action with its token and dollar cost |

---

## 8. Build phases

Each phase leaves something working and is committed separately.

| Phase | What | Done when |
|---|---|---|
| A | Architecture notes and migration plan | Tables, RLS rules and API endpoints written down |
| B | Supabase project, migrations, data copied from SQLite | Existing events, companies and briefs readable in Postgres |
| C | FastAPI over the existing code | Briefs, events, companies and Jobs work via the API, with tests |
| D | Worker | Daily run writes events and briefs for followed industries |
| E | Next.js front end | All redesign screens work against the API |
| F | Google sign-in, RLS, limits, metering | Two test users can't see each other's data; limits enforced |
| G | Deploy (Vercel, Render, Supabase prod), Sentry, spend caps | Live URL, errors reported, caps set |
| H | Ross launch | First users onboarded |

---

## 9. Success metrics

| Metric | Target |
|---|---|
| Brief and event pages load | < 2 seconds |
| Users who can read another user's private data | 0 (tested with RLS tests) |
| Uptime | Site reachable without my laptop on |
| Monthly cost at launch | Within the budget set before launch |
| Cost visibility | Every Claude and search call recorded with its cost |

---

## 10. Open decisions

- Resume-tailoring cap (suggested 5/day or ~30/month).
- Starting monthly budget and target launch date. Current estimate for a Ross launch: roughly $300–750/month, mostly Claude usage for Jobs.
- Where the chat agent lives in the new app (it was removed as its own page).
- Whether sign-in is limited to university Google accounts.
