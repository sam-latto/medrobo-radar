# Product Requirements Document — v2
# Industry Radar (evolved from MedRobo Radar)

**Version:** 2.0
**Status:** Ready to build
**Author:** Sam
**Last Updated:** September 2026
**Builds on:** `docs/PRD.md` (v1). Everything in v1 stays unless this doc changes it.

---

## 0. How to use this doc (for Claude Code)

Work through the phases **in order**. Each phase should leave the app runnable, with the healthcare robotics profile producing the same kind of output as v1. After each phase:
1. Run `python main.py run` and confirm the pipeline completes.
2. Run `streamlit run dashboard/app.py` and confirm the dashboard loads.
3. Commit with a message naming the phase.

Do not switch frameworks (keep Python, Streamlit, SQLite, APScheduler). Hosting / Next.js is out of scope for v2.

---

## 1. Goal

Turn MedRobo Radar from a single-industry tool (healthcare robotics) into a **multi-industry intelligence agent**:
- The user can track any industry by creating an **industry profile**. The agent can draft a profile from just an industry name.
- The pipeline pulls from **primary sources** (FDA, SEC, research databases) in addition to web search, so key facts are verifiable.
- The user can **ask questions in plain English** against the collected data (v1 roadmap item #1).
- The user can see **company profiles and a momentum ranking** (v1 roadmap item #2).

Healthcare robotics becomes the first profile, not hardcoded logic.

---

## 2. Phase 0 — Fixes to v1 (do first)

| # | Issue | Where | Fix |
|---|---|---|---|
| 0.1 | Query templates hardcode **"2025"**, biasing every search toward last year | `pipeline/search_agent.py` `QUERY_TEMPLATES` | Remove the year; pass Tavily a recency filter instead (e.g. `days=` / `time_range=` — check the current tavily-python API). Fallback: inject `date.today().year` dynamically. |
| 0.2 | Synthesis agent parses JSON from free text; a malformed reply silently drops to the fallback briefing | `pipeline/synthesis_agent.py` | Use tool use with forced `tool_choice` (same pattern as `extraction_agent.py`), schema: `briefing_text: str`, `additional_notable_indices: int[]`. |
| 0.3 | Extraction makes one Claude call per search result (up to ~125/day) | `pipeline/extraction_agent.py` | Batch 10–20 results per call; tool returns `events: ExtractedEvent[]`, each carrying its `source_url` so results map back. Keep per-item error isolation (if a batch fails, retry its items individually). |
| 0.4 | Scheduler can start **twice**: `main.py` default mode starts a BackgroundScheduler *and* `dashboard/app.py` starts another | `main.py`, `dashboard/app.py` | Remove scheduler startup from `dashboard/app.py`. The scheduler lives only in `main.py`. |
| 0.5 | `MODEL` is hardcoded in `config.py` | `config.py` | Read from env `ANTHROPIC_MODEL` with the current value as default. |

**Acceptance:** pipeline runs, briefing is always generated via tool use, number of extraction API calls drops ≥5×, only one scheduled job fires per day.

---

## 3. Phase 1 — Industry profiles (core refactor)

### 3.1 Profile format
Create `profiles/` with one YAML file per industry. Move all healthcare-robotics specifics here.

```yaml
# profiles/healthcare_robotics.yaml
id: healthcare_robotics
name: Healthcare Robotics
description: Robotic systems used in surgery, rehab, diagnostics and care delivery.
active: true
segments: [surgical, rehabilitation, diagnostics, exoskeletons, ai_assisted]
event_types: [funding, regulatory, launch, research, partnership_ma, news]
search_queries:            # {segment} is substituted; no years
  funding: "{segment} robotics company funding round investment"
  regulatory: "{segment} medical robot FDA clearance approval De Novo"
  launch: "{segment} robotics new product launch"
  research: "{segment} robotics clinical trial study results"
  partnership_ma: "{segment} robotics acquisition partnership"
  news: "{segment} healthcare robotics news"
notable_rules:
  funding_threshold_usd: 50000000
  always_notable_event_types: [regulatory, partnership_ma]
sources:                   # which source plug-ins run for this profile (Phase 2)
  tavily: {}
  openfda_510k:
    product_codes: []      # optional FDA product codes to narrow results
    keywords: [robot, robotic, "robotically-assisted"]
  sec_form_d:
    keywords: [robotics, surgical robot]
  pubmed:
    query: "(robotic surgery OR surgical robot OR rehabilitation robot)"
watchlist: [Intuitive Surgical, Medtronic, Stryker, CMR Surgical, Moon Surgical]
briefing_audience: "sophisticated healthcare investor"
```

Add a second and third profile to prove it generalizes: `profiles/physical_ai.yaml` (humanoids, warehouse/logistics robots, robot foundation models) and one industrial profile (e.g. `profiles/industrial_automation.yaml`).

### 3.2 Code changes
- New `profiles/loader.py`: load and validate profiles with a Pydantic `IndustryProfile` model.
- `pipeline/models.py`: remove hardcoded `allowed` sets from validators. Validate `event_type` / `segment` against the **active profile** (unknown → `news` / `other`).
- `pipeline/extraction_agent.py`: build the tool schema's enums and the prompt text from the profile.
- `pipeline/search_agent.py`: build queries from `profile.search_queries × profile.segments`.
- `pipeline/synthesis_agent.py`: industry name and audience come from the profile; notable rules come from `profile.notable_rules` (replaces `NOTABLE_FUNDING_THRESHOLD` and the hardcoded `fda` check).
- `pipeline/pipeline.py`: `run_pipeline(profile_id=None, ...)`. If `None`, loop over all active profiles. `_already_ran_today` becomes per-profile.
- Rename `sub_segment` → `segment` and `fda_status` → `regulatory_status` in models and DB (migration below).

### 3.3 Database changes (`database/db.py`)
- `events`: add `industry_id TEXT NOT NULL`, rename columns above, add `source_type TEXT` (`tavily` | `openfda` | `sec_form_d` | `pubmed` | ...), add `company_id INTEGER NULL`.
- Unique index becomes `(industry_id, source_url)` (the same article can matter to two industries).
- `pipeline_runs`: add `industry_id TEXT`.
- Write a one-time migration: existing rows get `industry_id = 'healthcare_robotics'`, `source_type = 'tavily'`. Back up `medrobo.db` before migrating.
- All query functions (`get_all_events`, `get_notable_events`, `get_latest_run`, ...) take an optional `industry_id`.

### 3.4 Agent-drafted profiles
New `pipeline/profile_agent.py`: `draft_profile(industry_name: str) -> IndustryProfile`.
- One Claude call with forced tool use that returns a full profile (segments, event types, queries, source settings, 5–15 watchlist companies).
- Saved as `profiles/<id>.yaml` with `active: false` so the user reviews before it runs.
- CLI: `python main.py new-profile "battery storage"`.

### 3.5 Dashboard
- Sidebar: **industry selector** (all active profiles) that filters every tab.
- Sidebar: "New industry" text input → calls `draft_profile`, shows the YAML in an editable text area, **Save & activate** button.
- Rename app title to **Industry Radar** (keep MedRobo Radar as the healthcare profile's display name).

### 3.6 Email
- One digest per profile, or one combined digest with a section per industry (configurable: `DIGEST_MODE=per_industry|combined`, default `combined`).

**Acceptance:** three profiles run in one scheduled job; dashboard switches between them; `new-profile` drafts a valid YAML; v1 healthcare data still shows after migration.

---

## 4. Phase 2 — Primary data sources

### 4.1 Source plug-in interface
Create `sources/` with a common interface:

```python
class Source(Protocol):
    name: str                      # e.g. "openfda_510k"
    def fetch(self, profile: IndustryProfile, since: date) -> list[RawResult]: ...
```

- `sources/tavily_source.py`: move current search logic here.
- `sources/registry.py`: maps names → classes; the pipeline runs every source listed in `profile.sources`.
- Add `source_type` to `RawResult`. Structured sources (FDA, SEC) may pre-fill fields so extraction can skip or shortcut them.
- Dedup against DB still uses `(industry_id, source_url)`.

### 4.2 Sources to add (all free; verify current endpoints and rate limits before coding)

| Source | What it gives | Endpoint / notes |
|---|---|---|
| **openFDA 510(k)** | Official device clearances: applicant, device name, decision date, product code | `https://api.fda.gov/device/510k.json` — search by `decision_date` range + keywords/product codes. Optional free API key raises limits. Also consider `device/pma.json` and De Novo. |
| **SEC EDGAR Form D** | Private fundraises: issuer, industry group, amount sold, date | EDGAR full-text search (`efts.sec.gov/LATEST/search-index?q=...&forms=D`) then the filing's `primary_doc.xml`. **SEC requires a descriptive `User-Agent` with contact email** and ≤10 requests/sec. |
| **PubMed** | Peer-reviewed research | NCBI E-utilities `esearch` + `esummary`. Optional API key. |
| **arXiv** | Preprints (useful for physical AI) | `http://export.arxiv.org/api/query` (Atom). Respect ~3s between calls. |
| **ClinicalTrials.gov** (optional) | New/updated trials | API v2 `https://clinicaltrials.gov/api/v2/studies` |

For each: add a config block to the profile schema, write unit tests with a saved sample response (no live calls in tests), and log counts per source per run.

### 4.3 Source trust
- Store `source_type` on every event; show it as a badge in the dashboard.
- In synthesis, tell Claude that FDA/SEC events are **authoritative** and news is **secondary**; when they conflict, prefer the primary source and say so.

**Acceptance:** a healthcare run shows FDA-sourced regulatory events with `accessdata.fda.gov` links; Form D events carry `funding_amount` from the filing, not from news.

---

## 5. Phase 3 — Ask questions (chat tab)

New dashboard tab **💬 Ask**, backed by `pipeline/query_agent.py`.

- Agentic loop using Claude tool use (the model picks tools, runs until it has an answer).
- Tools (all read-only, parameterized SQL — **never** let the model write raw SQL):
  - `search_events(industry_id?, segment?, event_type?, company?, date_from?, date_to?, min_funding?, text?, limit)`
  - `get_company(name)`: profile + event history (Phase 4)
  - `get_briefings(industry_id?, date_from?, date_to?)`
  - `aggregate_events(group_by: company|segment|event_type|month, filters...)`: counts and funding sums
- Every answer cites event IDs / source URLs; the UI renders them as links.
- Respects the sidebar industry selector as a default filter.
- Keep conversation history in `st.session_state`; cap tool-loop iterations (e.g. 8).

Example questions to test: "Which surgical robotics companies raised over $100M this year?", "Summarize all FDA clearances in the last 30 days", "Compare funding in exoskeletons vs rehab robots by quarter."

**Acceptance:** the three example questions return correct, cited answers that match what the Event Tracker tab shows.

---

## 6. Phase 4 — Company profiles & momentum

### 6.1 Companies table
- New `companies` table: `id, name, normalized_name, industry_ids (JSON), website, first_seen, last_seen`.
- During extraction, resolve `company` → `company_id` (normalize case/punctuation/suffixes like Inc/Ltd; ask Claude to match against the known list when fuzzy).

### 6.2 Momentum score
Per company, over a rolling 90-day window (weights live in the profile, sensible defaults):
- Funding events (log-scaled by amount)
- Regulatory milestones
- Launches and partnerships/M&A
- Research mentions
- News volume (lowest weight)

### 6.3 Dashboard
- New tab **🏢 Companies**: ranked momentum table per industry; click a company → timeline of events, total disclosed funding, regulatory history, source links.
- Watchlist companies from the profile are pinned/starred.

**Acceptance:** Companies tab ranks at least the watchlist; each company page shows its full event history.

---

## 7. Phase 5 — Optional / later

- **Hiring signal:** public job-board APIs (Greenhouse `boards-api.greenhouse.io/v1/boards/{token}/jobs`, Lever `api.lever.co/v0/postings/{company}`, Ashby `api.ashbyhq.com/posting-api/job-board/{name}`) for watchlist companies; track open-role counts over time.
- **Patents:** USPTO PatentsView API.
- **Briefing continuity:** feed the last 3 briefings into synthesis so it can say "following up on…" (v1 open question).
- **Retry/alert on failed runs** (v1 open question).
- Export filtered tables to CSV.

---

## 8. Out of scope (v2)

- Multi-user auth, hosting, or a Next.js rewrite
- Paid data sources (PitchBook, Crunchbase, Bloomberg)
- Scraping LinkedIn or any site whose terms forbid it

---

## 9. Success metrics

| Metric | Target |
|---|---|
| Profiles running in one scheduled job | ≥3 |
| Share of regulatory/funding events backed by a primary source | ≥50% (healthcare profile) |
| Extraction API calls per run vs v1 | ≤20% of v1 |
| Chat answers with correct citations on test questions | 3/3 |
| Duplicate events | <5% |

---

## 10. Suggested kickoff prompt for Claude Code

> Read `docs/PRD.md` and `docs/PRD_v2.md`, then the whole codebase. Implement **Phase 0** only. Keep the app runnable, add tests where practical, run the pipeline once, and summarize what changed. Stop before starting Phase 1.

Repeat per phase ("Implement Phase 1 …").
