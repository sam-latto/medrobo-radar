# Industry Radar

An autonomous AI agent pipeline that tracks any industry: it collects news and primary-source data, extracts structured events, writes a daily briefing, ranks companies by momentum, and answers plain-English questions over everything it has collected.

It started as **MedRobo Radar** (healthcare robotics), which is now the first of several industry profiles.

## Architecture

```
            ┌─ Tavily web search ─┐
Profiles →  ├─ openFDA 510(k)     ├→ Extraction Agent → Company resolution → Synthesis Agent → Dashboard + Email
 (YAML)     ├─ SEC Form D         │   (Claude, batched)   (companies table)    (Claude briefing)
            ├─ PubMed / arXiv     │
            └─ ClinicalTrials.gov ┘
                                                   Ask Agent (Claude tool loop, read-only SQL tools) ─→ 💬 Ask tab
```

1. **Sources** (`sources/`) — plug-ins listed under `sources:` in each profile. Primary sources (FDA, SEC, research databases) pre-fill authoritative fields such as the FDA decision or the amount sold in a Form D.
2. **Extraction Agent** — one Claude call per batch of ~15 results, with the tool schema's enums built from the profile.
3. **Company resolution** — links each event to a `companies` row (exact normalized match, or Claude confirms near-matches).
4. **Synthesis Agent** — writes the briefing via tool use and flags notable events using the profile's rules. Primary sources are treated as authoritative over news.
5. **Ask Agent** — answers questions with parameterized read-only tools (`search_events`, `aggregate_events`, `get_briefings`, `get_company`) and cites event ids.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # add your API keys

python main.py              # scheduler + Streamlit dashboard
streamlit run dashboard/app.py   # dashboard only
python main.py run          # run every active profile once
```

## CLI

| Command | What it does |
|---|---|
| `python main.py run [profile_id] [--force]` | Run the pipeline once (all active profiles by default; `--force` re-runs profiles that already ran today) |
| `python main.py new-profile "battery storage"` | Have Claude draft a new industry profile, saved inactive for review |
| `python main.py profiles` | List profiles |
| `python main.py backfill-companies` | Link existing events to company records |
| `python main.py scheduler` | Run only the daily scheduler |

## Industry profiles

Each industry is one YAML file in `profiles/` (see `profiles/healthcare_robotics.yaml`): segments, event types, search queries (with a `{segment}` placeholder and no years), notable-event rules, sources, watchlist, momentum weights and briefing audience. Three ship with the app: healthcare robotics, physical AI and industrial automation.

Create one from the dashboard (sidebar → ➕ New industry → review the YAML → **Save & activate**) or with `new-profile`.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key |
| `TAVILY_API_KEY` | Yes | Tavily search API key |
| `ANTHROPIC_MODEL` | No | Claude model (default `claude-sonnet-4-6`) |
| `SEC_USER_AGENT` | For SEC | SEC requires a descriptive User-Agent with a contact email, e.g. `IndustryRadar you@example.com`. The Form D source is skipped without it. |
| `OPENFDA_API_KEY`, `NCBI_API_KEY` | No | Optional keys that raise openFDA / PubMed rate limits |
| `SEARCH_TIME_RANGE` | No | Tavily recency filter: `day`, `week` (default), `month`, `year` |
| `EXTRACTION_BATCH_SIZE` | No | Search results per extraction call (default 15) |
| `DIGEST_MODE` | No | `combined` (default, one email with a section per industry) or `per_industry` |
| `EMAIL_FROM`, `EMAIL_TO` | No | Digest sender/recipient |
| `SENDGRID_API_KEY` or `SMTP_HOST/PORT/USER/PASSWORD` | No | Email transport |
| `PIPELINE_SCHEDULE_HOUR` / `_MINUTE` | No | Daily run time (default 07:00) |
| `PIPELINE_RETRY_DELAY_MINUTES` | No | A failed scheduled run is retried once after this delay (default 30), then an alert email is sent |
| `DB_PATH` | No | SQLite path (default `medrobo.db`) |

## Dashboard

The sidebar industry selector filters every tab.

- **📋 Event Tracker** — filterable table (segment, event type, source, dates, company) with source badges and CSV export
- **🔔 Notable Events** — high-signal feed from the last 7 days
- **📰 Briefing** — latest narrative briefing plus history
- **💬 Ask** — questions answered from the data, with cited sources
- **🏢 Companies** — 90-day momentum ranking (watchlist starred) and per-company timelines

## Data model

SQLite:
- `events` — one row per event per industry (unique on `industry_id, source_url`), with `source_type` and `company_id`
- `pipeline_runs` — one row per industry per run, with the briefing and per-source counts
- `companies` — normalized company records shared across industries

Opening a v1 database migrates it automatically (a backup is written next to it first); existing rows become `healthcare_robotics` events.

## Tests

```bash
python -m pytest
```

Source plug-ins are tested against saved sample responses in `tests/fixtures/`; no test makes a live API call.
