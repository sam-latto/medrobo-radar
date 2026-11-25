# Product Requirements Document — v3
# Industry Radar: from a local tool to a hosted web app

**Version:** 3.1
**Status:** Building. Phases A–D done, E in progress; staged launch from December 1, 2026 (section 10)
**Author:** Sam
**Last Updated:** November 2026
**Builds on:** `docs/PRD.md` (v1) and `docs/PRD_v2.md` (v2). The pipeline, sources, briefing and Jobs logic from v2 stay; this doc changes where they run, how they're stored and how people reach them.

**What changed in v3.1 (November 2026)**
- **Launch re-planned** (section 10): a staged launch. December 1 is a beta of Briefing, Events and Companies (with Consulting, PE and VC profiles); Jobs follows about two weeks later.
- **Product outcome metrics** added next to the engineering ones (section 9), with the instrumentation they need.
- **User stories and acceptance criteria** for every screen (section 11), and a **launch plan** with beta exit criteria (section 12).
- **Decisions made while building** recorded in section 5 (5.10–5.15): who the brief is written for, how it ranks stories, the model mix, link and duplicate rules, and the industry tabs.
- **Work tracking:** remaining work lives as GitHub issues under the *Ross launch* milestone; every change ships as a reviewed pull request.

---

## 0. How to use this doc

Sections 1–3 say what v3 is and who it's for. Section 4 is the architecture. Section 5 records each major decision with the reasoning, the alternatives considered and the trade-offs. Section 6 is the new repo structure, section 7 the data model, section 8 the build phases. Section 9 is how success is measured, section 10 the launch decisions, section 11 the user stories with acceptance criteria, and section 12 the launch plan.

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

### Decisions made while building (October–November 2026)

### 5.10 Write the brief for MBA readers, not investors

**Decision:** the brief is written for "MBA students and early-career professionals who follow this industry for recruiting, interviews and career moves", in the style of a business newspaper's industry coverage or a consulting firm's update. Jargon gets a short explanation.

**Why:** the brief's persona came from the single-user version, where each industry was written for a different investor ("deep-tech venture investor"). The first real brief read like a venture pitch ("the main investable battleground") and led with a neat thesis instead of the week's biggest news. The actual readers want to know who is growing, raising and hiring, and what big moves mean for the companies and people involved.

**Alternatives considered:**
- *Keep investor personas:* sharper financial analysis, but the wrong reader.
- *A blend (MBA reader who thinks like an investor):* tempting, but it pulls the voice back toward pitch language.
- *Personalize per user:* the brief is shared, so per-user rewriting would multiply cost (see 5.5).

**Trade-off:** less depth on valuation and deal terms. Investor-minded readers (PE and VC recruiting) still get the money facts, because deals rank first (5.11).

**In one sentence:** I wrote for the readers I actually have, not the persona the prototype was built for.

### 5.11 An explicit ranking decides what leads the brief

**Decision:** the brief ranks news in a fixed order: (1) large funding, M&A and regulatory approvals, (2) commercial traction such as big customers, deployments and hiring, (3) launches and new entrants, (4) leadership and strategy changes, (5) research only when decisive. Within a category, bigger and more concrete wins. Each industry profile can replace the order (`briefing_priorities`), because Consulting news looks nothing like FDA news. The writer is also handed the run's largest deals and regulatory actions, and a warning is logged if the brief skips one.

**Why:** the first brief left out the biggest round of the week ($152M) because the model optimized for a tidy theme. "Most important" was never defined, so the model defined it.

**Alternatives considered:** *leave it to model judgment* (cheapest, but inconsistent across industries); *have Claude revise briefs that miss a key item* (~$0.04 per run; held back until the warnings show it's needed).

**Trade-off:** a fixed order can feel formulaic on slow days. Accepted for consistency, which readers notice more.

**In one sentence:** I turned "pick what matters" from a model's judgment call into a stated, adjustable product rule.

### 5.12 Model mix: Haiku for extraction, Sonnet for writing

**Decision:** event extraction and company matching use Claude Haiku 5.5; the brief, job scoring and resume tailoring use Claude Sonnet 5.5. The nightly run uses the Message Batches API (50% off).

**Why:** extraction is high-volume, structured work; writing and judging fit is where quality shows. The first paid run cost $0.44 for one industry: search was 87% of that, the brief about 11%, and Haiku extraction under 2%. Sonnet 5.5 replaced 4.6 at a lower price ($2/$10 vs $3/$15 per million tokens).

**Trade-off:** Haiku made more mistakes (duplicates, a customer contract recorded as funding), so it needed guardrails (5.13) rather than a pricier model.

**In one sentence:** I spent on the model where quality is visible and fixed the cheap model's mistakes with rules instead of money.

### 5.13 Data hygiene: merge duplicates, reject redirect links, never guess dates

**Decision:**
- The same story from several outlets becomes one event, keeping the most authoritative source.
- Customer contracts, revenue and valuations are never recorded as funding; roundups and listing pages aren't events.
- Links that redirect (newsletter click trackers, shorteners, news aggregators) are never accepted as sources, even on trusted sites. Every link in a brief must be one of the collected sources, or it's removed.
- The writer never guesses a missing date, and it gets the opening of the source text for the biggest stories.

**Why:** the first real run had the same round counted up to three times, a $70M customer contract shown as a $70M raise, a newsletter tracking link as a "source", and a wrong date. Each one undermines "every claim is checkable" (5.9).

**In one sentence:** trust is the product, so I treated each data error from the first run as a rule to enforce, not a one-off to patch.

### 5.14 Industry tabs instead of a sidebar dropdown

**Decision:** the industry switcher is a row of tabs at the top of Briefing, Events and Companies, not a dropdown in the sidebar.

**Why:** in my review of the first build, switching industries from the sidebar felt disconnected from the page it changed, and it took two clicks. Tabs show every followed industry at once and change the page in one click.

**Trade-off:** the row needs to scroll sideways for people following many industries. Acceptable at launch, where most follow two to four.

**In one sentence:** I put the control next to what it changes.

### 5.15 Staged launch instead of everything on December 1

**Decision:** see section 10. Briefing, Events and Companies launch to a beta group on December 1; Jobs follows about two weeks later.

**Why:** with a week left, Events, Companies, Jobs and deployment were still open. Jobs is the largest remaining piece, the main cost driver, and the only feature handling private documents (resumes), so it carries the most risk.

**Alternatives considered:** *slip the whole launch* (complete, but loses the moment and runs into exams and break); *everything on December 1* (highest risk of a broken first impression).

**In one sentence:** I kept the date and cut scope to the part that's ready, putting the riskiest and costliest feature behind a short second release.

### 5.16 Start production on Supabase's free plan

**Decision:** production runs on the free Supabase plan during the beta and early launch, with a weekly database backup I run myself. Upgrade to Pro ($25/month) when any trigger below is hit.

**Why:** the beta is 10–15 users and the database is well under the free limits, so $25/month buys little yet. The free plan's pause-after-a-week-idle doesn't apply in practice, because the nightly worker uses the database every day (the dev project paused only because nothing ran for six weeks).

**What it gives up:** automatic daily backups (covered by a weekly `pg_dump` kept outside Supabase), and headroom (500 MB database, 1 GB file storage, 2 active projects).

**Upgrade to Pro when:** the full Ross launch opens; or the database passes 400 MB or file storage 800 MB; or students' resumes are stored (Jobs release), since that data deserves daily backups; or a pause or outage ever happens in production.

**In one sentence:** I don't pay for capacity before there's usage, but I wrote down exactly when the cheap option stops being good enough.

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

| Phase | What | Done when | Status (Nov 24) |
|---|---|---|---|
| A | Architecture notes, migration plan, design system | `docs/ARCHITECTURE.md` and `docs/DESIGN.md` written and approved | Done |
| B | Supabase project, migrations, data copied from SQLite | Existing events, companies and briefs readable in Postgres | Done |
| C | FastAPI over the existing code | Briefs, events, companies and Jobs work via the API, with tests | Done |
| D | Worker | Daily run writes events and briefs for followed industries | Done, plus a multi-agent code review and its fixes |
| E | Next.js front end | All redesign screens work against the API | In progress: sign-in, Choose industries, Briefing done; Events, Companies, Jobs open |
| F | Google sign-in, RLS, limits, metering | Two test users can't see each other's data; limits enforced | Done (sign-in moved into E; a real umich sign-in verified) |
| G | Deploy (Vercel, Render, Supabase prod), Sentry, spend caps | Live URL, errors reported, caps set | Not started |
| H | Ross launch | First users onboarded | Staged: beta Dec 1, see section 12 |

Every phase ships as a pull request into the previous one (A into `main`, B into A, and so on) with a description of the problem, the change and how it was tested. Remaining work is tracked as GitHub issues under the *Ross launch* milestone.

---

## 9. Success metrics

### Product outcomes (is it useful?)

The targets are starting hypotheses for a few hundred Ross users. The beta (section 12) sets the baseline, and the targets are revisited after four weeks.

| Metric | Definition | Target |
|---|---|---|
| **Reach** | Ross accounts created | 100 in the first month after the full launch |
| **Activation** | New users who follow at least one industry *and* open a brief within 7 days of signing up | ≥ 60% |
| **Weekly active readers** | Signed-up users who open a brief or the event tracker in a given week | ≥ 40% by week 4 |
| **Week-4 retention** | Users active in their 4th week after signing up | ≥ 30% |
| **Brief usefulness** | Share of "Was this brief useful?" answers that are yes | ≥ 70% |
| **Jobs adoption** (after Jobs ships) | Weekly active users who run Find jobs at least once | ≥ 25% |
| **Cost per active user** | Monthly spend ÷ monthly active users | ≤ $1.50 |

**North star:** weekly active readers. A brief people come back to every week is the core value; everything else (Companies, Jobs) builds on that habit.

**How it's measured:** a small `analytics_events` table written by the API (sign-up, industries chosen, brief opened, events opened, find jobs, tailor), plus a one-tap usefulness question under each brief. No third-party tracking: the data stays in our database and is covered by export and delete. (Backlog: "Product analytics".)

### Quality and cost guardrails (is it safe and affordable?)

| Metric | Target |
|---|---|
| Brief and event pages load | < 2 seconds |
| Users who can read another user's private data | 0 (tested with RLS tests) |
| Links in a brief that aren't one of the collected sources | 0 (enforced in code) |
| Uptime | Site reachable without my laptop on |
| Monthly cost at launch | ≤ $300 |
| Cost visibility | Every Claude and search call recorded with its cost |

---

## 10. Launch decisions (October 2026, revised November 2026)

| Decision | Choice | Why |
|---|---|---|
| Resume tailoring cap | 30 per user per month | Allows bursts during recruiting season while keeping the monthly cost predictable |
| Who can sign in | `@umich.edu` Google accounts only | Keeps the launch to the Ross community and contains cost; the communities table makes opening up later a data change |
| Existing data | Moved over as the starting data | New users see months of history on day one instead of an empty app |
| New industries | Consulting, Private equity, Venture capital (Banking later) | The sectors Ross students recruit into most; Banking waits until there's demand |
| Daily email | Opt-in, off by default | Avoids spamming classmates and keeps email cost at zero until people ask for it |
| Chat agent | Left out of the first release | Focus the launch on the brief, events, companies and Jobs; add chat once the core is solid |
| Budget | ~$300/month | Covers hosting plus Claude and search within the usage limits |
| Launch date | ~~December 1, 2026, everything~~ **Revised Nov 24:** staged. Dec 1: beta of Briefing, Events and Companies (incl. Consulting, PE, VC) for 10–15 students. Full Ross launch when the beta exit criteria are met. Jobs about two weeks after Dec 1 | One week out, Events, Companies, Jobs and deployment were still open. Jobs is the biggest, costliest and only resume-handling piece, so it ships second (5.15) |
| Brief audience | **Added Nov:** MBA readers, fixed story ranking | 5.10, 5.11 |
| Models | **Added Nov:** Haiku 5.5 for extraction, Sonnet 5.5 for writing and Jobs | 5.12 |

Details of how these are enforced are in `docs/ARCHITECTURE.md`. The look and component rules are in `docs/DESIGN.md`.

---

## 11. User stories and acceptance criteria

Each story is one GitHub issue under the *Ross launch* milestone. "Done" means every criterion below is met, it works at 375px wide, it follows `docs/DESIGN.md`, it has empty, loading and error states, and it's covered by tests where there's logic. Release: **Beta** = December 1, **Jobs** = second release.

### 11.1 Sign in and onboarding (Beta, done)
**As a** Ross student, **I want to** sign in with my school Google account and pick the industries I follow, **so that** I only see what's relevant to my recruiting.
- [x] "Continue with Google" signs in with a `@umich.edu` account; any other account sees "open to University of Michigan accounts only" and gets no account.
- [x] A first-time user lands on "Which industries do you follow?" and can't continue with nothing selected.
- [x] Choosing a sector follows all its industries; search filters the list; at most 25 industries.
- [x] Typing an unlisted area ("Surgical robotics") either adds it as a focus of an existing industry or sends it for review, and says which.
- [x] Industries without an active brief say "Briefs start soon".
- [x] The private-data promise is shown on the sign-in page.

### 11.2 Briefing (Beta, done)
**As a** student preparing for interviews, **I want** a short research brief for each industry I follow, **so that** I can speak to what changed and why it matters.
- [x] One tab per followed industry switches the brief in one click.
- [x] The brief shows headline, dek, "The bottom line", "By the numbers", themed sections, "Why it matters" and "What to watch"; older briefs still read cleanly.
- [x] Every source is a numbered superscript linking to a source list; links open in a new tab.
- [x] A date picker shows past briefs; PDF prints the brief without the app chrome.
- [x] Side column: deal and regulatory trackers (last 90 days, no duplicates) and the user's focus matches.
- [x] An industry with no brief yet explains when the first one arrives.
- [ ] A one-tap "Was this brief useful?" (yes/no) under the brief, recorded for the usefulness metric.

### 11.3 Event tracker (Beta)
**As a** student researching an industry, **I want** a searchable list of every tracked event with its source, **so that** I can find specific deals, approvals and launches.
- [ ] The industry tabs from Briefing sit at the top and switch the list.
- [ ] Table columns: date, company, event (summary), type, source link. Dates read "Sep 29".
- [ ] Filters: event type, segment, date range and text search; filters combine and show the result count.
- [ ] "Only my focus" shows events matching the user's focus, when one is set.
- [ ] Pagination shows "Showing 1–25 of 143" with Previous/Next.
- [ ] Export CSV downloads the current filters (up to 5,000 rows).
- [ ] Each row has a Watch button for its company.
- [ ] At 375px the table scrolls sideways inside its box, never the page.

### 11.4 Companies (Beta)
**As a** student targeting employers, **I want to** see which companies are most active and what each one does, **so that** I know the names to know and can watch the ones I care about.
- [ ] A searchable list for the selected industry, ranked by momentum, with "what they do" and the last activity date.
- [ ] "Watching only" shows the user's watched companies.
- [ ] Watch / Watching toggles in one click and persists (up to 500 per user).
- [ ] Clicking a company opens its profile: description, recent events with sources, filings, trials and hiring where available, and similar companies.
- [ ] A company with little data says so instead of showing empty sections.

### 11.5 Jobs: resumes and preferences (Jobs release)
**As a** job seeker, **I want to** upload my resumes and say what roles I want, **so that** matches are scored against the right resume.
- [ ] Upload PDF, Word (.docx) or text; at most 5 resumes; other files get a clear message.
- [ ] Each resume can be mapped to the role types it's for; delete asks for confirmation.
- [ ] Preferences: role types, locations (plus remote / remote only), seniority and industries, each a searchable multi-select.
- [ ] "Your resume, job preferences and saved industries stay private to you" is shown here.

### 11.6 Jobs: find and score (Jobs release)
**As a** job seeker, **I want** roles from companies in my industries scored against my resume, **so that** I spend time on the best fits.
- [ ] "Find jobs" shows "1 left today"; it's limited to once a day, and at the limit it says when it resets.
- [ ] While running, a progress line shows the current step ("Scoring 12 of 40 jobs…"); leaving and returning keeps the progress.
- [ ] Matches show a score badge, company, title, location and a "Top match" highlight on the best one; no seniority labels.
- [ ] Filters: role type, location, industry; each job description is collapsible; "Open posting" opens the original.
- [ ] Postings that closed or moved are marked as such.

### 11.7 Jobs: tailor my resume (Jobs release)
**As a** job seeker, **I want** specific suggestions to tailor my resume to a role, **so that** my application fits it.
- [ ] "Tailor resume" on a job shows "24 of 30 left this month"; the text turns to warning at 80% used.
- [ ] Results appear with progress while running (30–60 s); a job already tailored shows the saved advice without using a credit.
- [ ] A failed tailoring is refunded if no paid call was made, and says so.
- [ ] At the limit: "You've used all 30 resume tailorings this month. They reset on <date>."

### 11.8 Account and privacy (Beta)
**As a** user, **I want to** control my data, **so that** I can trust the app with my resume.
- [ ] Account menu: display name, opt in or out of the daily brief email (off by default).
- [ ] "Download my data" exports everything the user owns as JSON.
- [ ] "Delete my account" asks for confirmation, then removes the account, private rows and files.
- [ ] Terms and Privacy pages exist and are linked from sign-in.

### 11.9 Operations (Beta)
**As the** owner, **I want** the app to run without my laptop and alert me to problems, **so that** I can run it alongside school.
- [ ] Web on Vercel, API and nightly worker on Render, production Supabase with migrations applied, all secrets in the host settings.
- [ ] Sentry reports errors from the web app, API and worker.
- [ ] Spend caps set in the Anthropic and Tavily consoles; budget alert at 80% reaches me.
- [ ] The Google sign-in app is published (out of "Testing") so any umich account can sign in.
- [ ] `analytics_events` records sign-up, industries chosen, brief opened, events opened, find jobs and tailor.

---

## 12. Launch plan

| Stage | When | Who | What ships | Move on when |
|---|---|---|---|---|
| **Beta** | Dec 1 | 10–15 Ross classmates, invited by me | Briefing, Events, Companies; Consulting, PE and VC profiles; feedback form | Exit criteria below are met (target: about 2 weeks) |
| **Jobs** | ~Dec 15 | Beta group first | Jobs (11.5–11.7) | No private-data or cost issues after one week of beta use |
| **Ross launch** | After the beta exit criteria | Ross MBA community (club channels, class Slack) | Everything | — |

**Beta exit criteria**
- No open bug that exposes private data, shows a wrong source, or blocks sign-in.
- At least 60% of beta users opened a brief in week 2 (early read on the north star).
- Usefulness answers at least 70% yes, or a fix is shipped for the top complaint.
- Nightly runs completed for every followed industry on at least 5 of the last 7 days.
- Cost per active user on track for ≤ $1.50 per month.

**Feedback loop:** a short form linked in the app (what's useful, what's missing, what's wrong), five 15-minute conversations with beta users in week 1, and a weekly look at the metrics in section 9. Every piece of feedback that leads to work becomes a GitHub issue.

**Rollback:** each release is a merge to `main` that Vercel and Render deploy. If a release breaks sign-in, privacy or sources, I revert the merge, which redeploys the previous version, and tell beta users what happened.

**Communication:** a personal invite to beta users explaining what it is, what's in the beta, and where to send feedback. For the Ross launch, a short post with a screenshot of a real brief and the privacy promise.
