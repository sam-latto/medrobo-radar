# MedRobo Radar

An autonomous AI agent pipeline that searches, extracts, and synthesizes healthcare robotics intelligence into a daily briefing and interactive dashboard for investors and analysts.

## Architecture

```
Search Agent → Extraction Agent → Synthesis Agent → Dashboard + Email
  (Tavily)      (Claude tool use)   (Claude prose)    (Streamlit)
```

Three sequential agents:
1. **Search Agent** — queries Tavily across event types × sub-segments, deduplicates against the database
2. **Extraction Agent** — calls Claude with tool use to extract structured fields from each result
3. **Synthesis Agent** — generates a narrative briefing and flags high-signal notable events

## Setup

```bash
# 1. Clone and install
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your API keys

# 3. Launch (scheduler + dashboard)
python main.py

# Dashboard only
streamlit run dashboard/app.py

# Run pipeline once manually
python main.py run
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key |
| `TAVILY_API_KEY` | Yes | Tavily search API key |
| `EMAIL_FROM` | No | Sender address for digest |
| `EMAIL_TO` | No | Recipient address for digest |
| `SENDGRID_API_KEY` | No | SendGrid transport (preferred) |
| `SMTP_HOST/PORT/USER/PASSWORD` | No | SMTP transport (fallback) |
| `PIPELINE_SCHEDULE_HOUR` | No | Hour for daily run (default: 7) |
| `NOTABLE_FUNDING_THRESHOLD` | No | USD threshold for notable funding (default: 50000000) |

## Dashboard Views

- **Event Tracker** — filterable table by sub-segment, event type, date range, company
- **Notable Events** — high-signal alerts feed with event badges
- **Briefing** — narrative summary with history of past runs

## Data Model

SQLite database with two tables:
- `events` — structured event records with deduplication on `source_url`
- `pipeline_runs` — run metadata and generated briefing text

## Tech Stack

| Layer | Tool |
|---|---|
| Agents | Anthropic Python SDK (claude-sonnet-4-6) |
| Web search | Tavily API |
| Validation | Pydantic v2 |
| Database | SQLite |
| Dashboard | Streamlit |
| Email | SendGrid / SMTP |
| Scheduling | APScheduler |
