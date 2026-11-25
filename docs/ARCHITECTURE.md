# Architecture — Industry Radar web app (Phase A)

**Status:** Draft for review
**Last Updated:** October 2026
**Companion to:** `docs/PRD_v3.md` (the what and why). This doc is the how: tables, access rules, API endpoints, the daily worker, limits and the data migration. Visual rules live in `docs/DESIGN.md`.

---

## 1. Launch decisions this design is built on

| Decision | Value |
|---|---|
| Who can sign in | Google accounts ending in `@umich.edu` only |
| Launch date | December 1, 2026 |
| Monthly budget | ~$300, all services included |
| Find & score jobs | 1 per user per day |
| Resume tailoring | 30 per user per calendar month |
| Daily brief email | Opt-in, off by default |
| Chat agent | Not in the first release |
| Existing data | Shared industry data from `medrobo.db` becomes the starting data; Sam's resumes and job data move to Sam's account |
| New industries | Consulting, Private equity, Venture capital (Banking later) |

---

## 2. System overview

```
Next.js (Vercel) ──HTTPS + JWT──► FastAPI (Render web service) ──► Supabase Postgres
      │                                   │                          + Auth + Storage
      └── Supabase Auth (Google) ─────────┘                                ▲
                                                                           │
                              Pipeline worker (Render cron, daily) ────────┘
```

- **Next.js** only talks to FastAPI for data. It uses the Supabase client only for sign-in and sign-out.
- **FastAPI** is the only service that holds the Anthropic, Tavily and SendGrid keys. It connects to Postgres as a role that respects row-level security for user requests.
- **The worker** connects with a service role (bypasses RLS) because it writes shared industry data. It never serves user requests.

---

## 3. Authentication

1. The user clicks "Continue with Google" and Supabase Auth runs the Google sign-in.
2. Google is configured with the `hd=umich.edu` hint, but that's only a hint, so the real check is server-side: a Postgres trigger on new auth users rejects any email not ending in `@umich.edu`. The API checks it again on every request.
3. Supabase issues a JWT. Next.js sends it as `Authorization: Bearer <token>` on every API call.
4. FastAPI verifies the JWT signature with the Supabase JWT secret, reads the user id (`sub`), and loads the user's profile.
5. For database queries made on a user's behalf, FastAPI sets the request's user id in the Postgres session (`request.jwt.claims`), so RLS policies apply exactly as if the user queried directly.

**First sign-in:** a trigger creates the `profiles` row with `community_id` = Ross. The front end sends a user with no subscriptions to "Choose industries".

---

## 4. Data model

All ids are `uuid` unless noted. Every table has `created_at timestamptz default now()`. Time is stored in UTC.

### 4.1 Communities and users

```sql
communities (
  id uuid pk,
  slug text unique,              -- 'ross'
  name text,                     -- 'Michigan Ross'
  email_domain text,             -- 'umich.edu'
  plan_id uuid fk -> plans       -- default plan for members
)

profiles (
  id uuid pk fk -> auth.users,   -- same id as the Supabase auth user
  community_id uuid fk -> communities,
  email text,
  display_name text,
  plan_id uuid fk -> plans null, -- override; null = community's plan
  email_brief_opt_in boolean default false,
  onboarded_at timestamptz null,
  deleted_at timestamptz null
)
```

### 4.2 Industries (shared catalog)

```sql
industries (
  id text pk,                    -- 'healthcare_robotics', 'consulting'
  parent_id text fk -> industries null,  -- sector, e.g. 'healthcare'
  name text,
  kind text,                     -- 'sector' (a group) | 'industry' (followable, can run a pipeline)
  profile_yaml text null,        -- pipeline config (today's profiles/*.yaml)
  status text,                   -- 'active' | 'needs_review' | 'retired'
  sort_order int
)

custom_industry_requests (
  id uuid pk,
  user_id uuid fk -> profiles,
  label text,                    -- what the user typed, e.g. 'Surgical robotics'
  matched_industry_id text fk -> industries null,  -- set when it fits an existing one
  status text                    -- 'focus' | 'pending_review' | 'approved' | 'rejected'
)
```

- A sector with sub-industries (Technology, Healthcare, Financial services…) is `kind = 'sector'`; following it means following all its sub-industries. Areas with no sub-industries (Consulting, Real estate…) are a single `kind = 'industry'` row with no parent.
- Private equity and Venture capital sit under Financial services (Banking to be added later).
- The pipeline only runs industries with `profile_yaml` set, `status = 'active'`, and at least one follower.
- The three existing YAML profiles seed `profile_yaml` for healthcare_robotics, physical_ai and industrial_automation. Other catalog entries get a profile drafted by `profile_agent.py` and reviewed by Sam before they turn active.

### 4.3 Shared industry data (written by the worker)

```sql
pipeline_runs (
  id uuid pk, industry_id text fk, run_date date,
  triggered_by text, status text, source_counts jsonb,
  cost_usd numeric(10,4), error text null, completed_at timestamptz
)

events (
  id bigint pk identity, run_id uuid fk, industry_id text fk,
  company_id bigint fk -> companies null, company text, product text,
  event_type text, segment text, date date, summary text,
  source_url text, source_type text,
  funding_amount bigint null, regulatory_status text null,
  unique (industry_id, source_url)
)

briefs (
  id uuid pk, industry_id text fk, run_id uuid fk, brief_date date,
  headline text, content_md text, content jsonb,   -- structured sections
  unique (industry_id, brief_date)
)

companies (
  id bigint pk identity, name text, normalized_name text unique,
  description text null,         -- "what they do"
  website text null, first_seen date, last_seen date
)

company_industries (company_id bigint fk, industry_id text fk, pk (company_id, industry_id))
```

- `briefs` is new. Today the brief is `pipeline_runs.briefing_text`; it moves to its own table so a run and its brief can be retried separately.
- `companies.industry_ids` (JSON list today) becomes the `company_industries` join table.
- `is_notable` is dropped. The redesign removed the Notable page, and the brief replaces it.

### 4.4 Jobs (shared postings, private everything else)

```sql
job_boards (provider text, token text, company text, last_fetched_at timestamptz, pk (provider, token))
job_board_checks (normalized_name text pk, checked_at timestamptz)

job_postings (            -- shared: a posting is the same for every user
  id bigint pk identity, url text unique, provider text, board_token text,
  company text, title text, location text, remote boolean null,
  department text, employment_type text, compensation text, description text,
  posted_at text, active boolean, first_seen_at timestamptz, last_seen_at timestamptz
)
job_posting_industries (job_id bigint fk, industry_id text fk, pk (job_id, industry_id))
```

Per-user (all have `user_id uuid not null fk -> profiles`):

```sql
resumes (
  id uuid pk, user_id, filename text, storage_path text,  -- file in Supabase Storage
  text text, role_types text[]    -- which role types this resume is for
)

job_preferences (
  user_id pk, role_types text[], custom_role_types text,
  locations text[], include_remote boolean, remote_only boolean,
  seniority text[], industry_ids text[]
)

job_matches (
  user_id, job_id bigint fk, resume_id uuid fk,
  score int, summary text, strengths jsonb, gaps jsonb, level_fit text,
  industry_match boolean, pk (user_id, job_id, resume_id)
)

resume_advice (
  user_id, resume_id uuid fk, kind text, key text, content jsonb,
  pk (user_id, resume_id, kind, key)
)
```

`app_settings` is split up: the job preferences move to `job_preferences`; the role-type keyword cache in `jobs/functions.py` becomes a shared `cache` table (key, value jsonb, expires_at).

### 4.5 Subscriptions and watchlist (per user)

```sql
subscriptions (
  user_id uuid fk, industry_id text fk,
  focus text null,               -- custom focus within that industry, e.g. 'Surgical robotics'
  pk (user_id, industry_id)
)

watchlist (user_id uuid fk, company_id bigint fk, pk (user_id, company_id))
```

The watchlists in today's YAML profiles stay as each industry's default "key companies" for the pipeline. The user's own Watch button writes to `watchlist`.

### 4.6 Plans and usage

```sql
plans (
  id uuid pk, name text,                    -- 'Free (Ross)'
  limits jsonb                              -- {"find_jobs": {"per": "day", "max": 1},
                                            --  "tailor_resume": {"per": "month", "max": 30}}
)

usage_events (
  id bigint pk identity,
  user_id uuid null,                        -- null for worker activity
  industry_id text null,
  action text,                              -- 'find_jobs' | 'tailor_resume' | 'score_jobs'
                                            -- | 'pipeline_extract' | 'brief' | 'search' ...
  model text null, input_tokens int, output_tokens int,
  cost_usd numeric(10,4),
  created_at timestamptz
)
create index on usage_events (user_id, action, created_at);
```

---

## 5. Row-level security

RLS is turned on for **every** table. Policies:

| Tables | Read | Write |
|---|---|---|
| `communities`, `industries`, `plans` | Any signed-in user | Service role only |
| `events`, `briefs`, `companies`, `company_industries`, `pipeline_runs` | Any signed-in user | Service role only (worker) |
| `job_postings`, `job_posting_industries`, `job_boards` | Any signed-in user | Service role only |
| `profiles` | Own row (`id = auth.uid()`) | Own row, but not `plan_id` or `community_id` |
| `subscriptions`, `watchlist`, `resumes`, `job_preferences`, `job_matches`, `resume_advice`, `custom_industry_requests` | Own rows (`user_id = auth.uid()`) | Own rows |
| `usage_events` | Own rows | Service role only (so users can't erase their usage) |

**Storage:** the `resumes` bucket is private. Object paths start with the user id (`<user_id>/<file>`), and the bucket policy only allows access to paths under your own id.

**Tests:** an automated test signs in as user A and user B and checks that B gets zero rows from each private table for A's data, and can't write to shared tables.

---

## 6. API (FastAPI)

All endpoints are under `/api/v1`, require a valid JWT except `/health`, and return JSON. Errors use one shape: `{"error": {"code": "limit_reached", "message": "...", "retry_after": "2026-12-02T00:00:00Z"}}`.

### Account and onboarding
| Method | Path | What it does |
|---|---|---|
| GET | `/me` | Profile, plan limits, usage this period, subscriptions |
| PATCH | `/me` | Display name, `email_brief_opt_in` |
| GET | `/me/export` | All the user's data as a JSON download |
| DELETE | `/me` | Deletes the account and all private rows and files |

### Industries
| Method | Path | What it does |
|---|---|---|
| GET | `/industries` | Catalog tree (sectors and sub-industries) |
| PUT | `/subscriptions` | Replace the user's followed industries (list of ids and focus) |
| POST | `/industries/requests` | Custom industry. Returns either "added as a focus of X" or "sent for review" |

### Briefing and events
| Method | Path | What it does |
|---|---|---|
| GET | `/briefs?industry_id=&date=` | Brief for an industry (latest if no date) |
| GET | `/briefs/dates?industry_id=` | Dates with a brief, for the date picker |
| GET | `/events?industry_id=&type=&segment=&from=&to=&q=&page=` | Event tracker, paged |
| GET | `/events.csv?…` | Same filters, CSV download |

### Companies
| Method | Path | What it does |
|---|---|---|
| GET | `/companies?industry_id=&q=&watching=` | Searchable list with momentum and "what they do" |
| GET | `/companies/{id}` | Profile: events, filings, trials, hiring, similar companies |
| PUT / DELETE | `/watchlist/{company_id}` | Watch or unwatch |

### Jobs
| Method | Path | What it does |
|---|---|---|
| GET / POST / DELETE | `/resumes`, `/resumes/{id}` | List, upload (PDF/DOCX, text extracted), delete |
| PATCH | `/resumes/{id}` | Set the role types this resume covers |
| GET / PUT | `/job-preferences` | Role types, locations, seniority, industries |
| POST | `/jobs/search` | **Metered (1/day).** Scores the active postings in the user's industries (collected daily by the worker) against their resumes; returns a task id |
| GET | `/tasks/{task_id}` | Progress, result or error of a search or tailoring task |
| GET | `/jobs/matches?…` | Scored matches with filters |
| POST | `/jobs/{job_id}/tailor` | **Metered (30/month).** Starts tailoring; returns a task id. Already-tailored jobs return the saved advice and aren't charged |
| GET | `/jobs/{job_id}/tailor` | Saved tailoring advice, or progress if running |

**Long tasks:** find & score and tailoring take 30–60+ seconds, so they run as background tasks. The API returns a task id right away, and the front end polls every few seconds. At launch scale, a simple `tasks` table plus FastAPI background workers is enough; a queue (e.g. Redis) can come later.

```sql
tasks (id uuid pk, user_id uuid, kind text, status text, progress jsonb,
       result jsonb null, error text null, created_at, updated_at)
```

---

## 7. Usage limits and metering

**Before a metered action:** the API counts the user's `usage_events` for that action in the current period (UTC day for `find_jobs`, calendar month for `tailor_resume`) and compares with the plan's limit. If the user is at the limit it returns `429` with `limit_reached` and when it resets. Only successful runs count, and the check and insert happen in one transaction so double clicks can't go over.

**On every Claude call:** `pipeline/llm.py` (`call_tool`) reports model, tokens and cost (prices in `pipeline/pricing.py`) to a per-task collector, which writes `usage_events` rows with action `llm:<tool name>`, tagged with the user (API) or the industry (worker, Phase D). Search (Tavily) cost logging follows in Phase D.

**Why job search doesn't search the web live:** collecting postings from job boards is slow (minutes) and the same for every user in an industry, so the worker refreshes postings once a day (Phase D) and a user's search only filters and scores them. Faster for the user and cheaper per search.

**Spend controls (budget ~$300/month):**
- Hard monthly spend limits set in the Anthropic and Tavily consoles.
- A daily check in the worker sums `usage_events.cost_usd` for the month; at 80% of budget it emails Sam, at 100% it pauses tailoring and new industry pipelines (briefs keep running).

---

## 8. Pipeline worker

`worker/run_daily.py`, run once a day by a Render cron job (early morning US Eastern), replaces the APScheduler in `main.py`.

1. Find industries to run: `status = 'active'`, has `profile_yaml`, and has at least one subscriber.
2. For each, call the existing `run_pipeline` logic (collect → extract → companies → brief) against Postgres. Industries run one after another to stay within API rate limits.
3. Recheck job postings for closed/moved roles (existing `recheck_postings`).
4. Send the daily email to users with `email_brief_opt_in = true`, one email per user covering their industries.
5. Write the run's cost to `pipeline_runs.cost_usd`, run the budget check, and send a failure alert if any industry failed (existing retry/alert logic).

Custom focuses (e.g. "Surgical robotics" inside Healthcare robotics) don't create new runs. They filter what that user sees and are passed to brief writing as an extra emphasis later if needed.

---

## 9. Moving the existing data

A one-time script, `scripts/migrate_sqlite_to_postgres.py`, run against the dev database first and then prod:

1. **Open `medrobo.db` read-only**, so the local file can't be changed by the copy.
2. **Catalog:** insert sectors and sub-industries, including Consulting, Private equity and Venture capital. Load the three YAML profiles into `profile_yaml`.
3. **Shared data:** copy `pipeline_runs`, `events`, `companies` (+ `company_industries`), `job_boards`, `job_board_checks`, `job_postings` (+ `job_posting_industries`). Move each run's `briefing_text` into `briefs`. Map SQLite integer run ids to new uuids.
4. **Sam's personal data:** after Sam signs in once (so the account exists), copy `resumes`, `job_matches`, `resume_advice` and the job preferences from `app_settings` with `user_id` = Sam's id. Upload resume files to Storage if the originals are available; otherwise keep the extracted text.
5. **Subscriptions:** subscribe Sam to the three existing industries.
6. **Check:** row counts match between SQLite and Postgres for each table; spot-check a brief, an event and a job match in the API.

The local SQLite app keeps working until the new app is live, so nothing is lost if the migration needs to be rerun.

---

## 10. Environments and deployment

| | Dev | Prod |
|---|---|---|
| Supabase | `industry-radar-dev` project | `industry-radar-prod` project |
| API + worker | Run locally (`uvicorn`), or a Render preview | Render web service + Render cron job |
| Front end | `next dev` locally, Vercel preview per branch | Vercel production (main branch) |
| Data | Copy of the migrated data | Real data |

- **Migrations:** SQL files in `supabase/migrations/`, applied with `python scripts/db_migrate.py` (records them in the same table the Supabase CLI uses, so the CLI can take over later). `supabase/seed.sql` holds plans, communities and the industry catalog. Every schema change is a new file, committed to git, applied to dev first.
- **Database tests:** `RUN_DB_TESTS=1 python -m pytest tests/test_supabase_rls.py` checks the sign-in rule and row-level security against dev, inside a transaction that's rolled back.
- **Secrets:** stored in Render and Vercel environment settings, never in git. `.env.example` lists the names.
- **Errors:** Sentry in the API, worker and front end (free tier).
- **Accounts:** Supabase, Vercel, Render, Sentry and the Google OAuth client are all under sam.rubin.latto@gmail.com, never the work account.

---

**Running the API locally:** `uvicorn api.main:app --reload`, then open http://localhost:8000/api/v1/docs. End-to-end tests: `RUN_DB_TESTS=1 python -m pytest tests/test_api.py tests/test_supabase_rls.py` (run against dev in a rolled-back transaction, with Claude and Supabase Storage faked).

---

## 11. Monthly cost estimate (at launch)

| Item | Estimate |
|---|---|
| Supabase Pro | $25 |
| Render (API web service + cron worker) | ~$15–25 |
| Vercel (Hobby, or Pro at $20 if needed) | $0–20 |
| Sentry, SendGrid (free tiers) | $0 |
| Claude + Tavily for industry pipelines (~6–10 active industries, optimized) | ~$60–100 |
| Claude for Jobs (scoring + tailoring, a few hundred users within limits) | ~$100–150 |
| **Total** | **~$200–320** |

Jobs is the swing item. If real usage runs hot, lower the tailoring cap or use a cheaper model for scoring before raising the budget.

---

## 12. Repo layout

As in PRD v3 section 6, plus:
- `scripts/migrate_sqlite_to_postgres.py` (one-time move)
- `database/` becomes a Postgres data layer (`psycopg` with a connection pool); the SQLite functions are replaced one module at a time, keeping the same function names so `pipeline/` and `jobs/` change as little as possible.

---

## 13. Risks and open questions

- **Workday and web job search are slow.** If find & score regularly takes several minutes, move tasks to a proper queue.
- **Profiles for new industries need review.** Consulting, PE and VC need profiles that fit their news (deals, fund raises, partner moves, not FDA clearances). Draft them early so they're tested before December 1.
- **Google sign-in restriction.** Some Ross students may prefer personal Gmail; the domain rule can be relaxed later by adding allowed emails to the community.
- **Tailoring quality at scale.** Walk through the tailoring prompt with Sam before launch (pending).
