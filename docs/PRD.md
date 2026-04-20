# Product Requirements Document
# MedRobo Radar

**Version:** 1.0  
**Status:** Draft  
**Author:** Sam  
**Last Updated:** April 2026

---

## 1. Overview

### Problem Statement
Healthcare investors and analysts tracking the robotics space face three compounding challenges: the landscape moves too fast to monitor manually, relevant information is scattered across news outlets, research databases, regulatory filings, and company blogs, and distinguishing high-signal developments from noise requires significant domain expertise and time.

### Product Goal
Build an autonomous multi-agent research pipeline that continuously monitors the healthcare robotics landscape, extracts and structures key developments, and delivers actionable intelligence through an interactive dashboard and daily email digest.

### One-Line Description
MedRobo Radar is an autonomous AI agent pipeline that searches, extracts, and synthesizes healthcare robotics intelligence into a daily briefing and interactive dashboard for investors and analysts.

---

## 2. User Personas

### Primary: Healthcare Investor / Analyst
- **Context:** Tracks the healthcare robotics space across surgical, rehabilitation, diagnostic, and other sub-segments
- **Pain points:** Spends hours manually aggregating news, struggles to track regulatory milestones and funding events in real time, finds it hard to surface trends across a fragmented information landscape
- **Goal:** Start each morning with a clear, structured view of what happened overnight and what it means for the market
- **Success looks like:** Opens their email digest at 8am, gets the key developments in 2 minutes, drills into the dashboard for specifics when needed

---

## 3. Core Features

### 3.1 Research Pipeline (Backend)
- Runs automatically every morning on a daily schedule
- Searches broadly across all healthcare robotics sub-segments: surgical, rehabilitation, diagnostics, exoskeletons, AI-assisted procedures, and adjacent categories
- Tracks five event types across the space:
  - Funding rounds and M&A activity
  - FDA clearances and regulatory milestones
  - New product launches
  - Academic research and clinical trial results
  - General company and market news
- Deduplicates results across runs to avoid surfacing the same story twice
- Persists all structured data to a local SQLite database

### 3.2 Dashboard (Frontend — Streamlit)
Three primary views:

**Company & Product Tracker Table**
- Filterable by sub-segment (surgical, rehabilitation, diagnostics, etc.), event type, date range, and company name
- Columns: Company, Product, Event Type, Date, Source, Summary
- Sortable by any column

**Notable Events / Alerts Feed**
- Highlights the highest-signal developments from the latest pipeline run
- Each alert includes: event type badge, one-sentence summary, source link, and date
- Flagging logic: large funding rounds (>$50M), FDA clearances, M&A announcements, top-cited research

**Auto-Generated Written Briefing**
- Narrative summary produced by the Synthesis Agent after each pipeline run
- Covers: top stories, emerging trends, companies to watch
- Displayed as a readable panel in the dashboard; also delivered via email

### 3.3 Email Digest
- Sends automatically each morning after the pipeline completes
- Contains: date, auto-generated briefing text, list of notable events with links
- Delivered via SendGrid or SMTP

### 3.4 Manual Refresh
- "Run Pipeline Now" button in the dashboard triggers an on-demand run outside the scheduled window
- Useful for breaking news or ad hoc research sessions

---

## 4. Agent Architecture

The pipeline is composed of three sequential agents, each with a defined role, toolset, and input/output contract.

### Agent 1 — Search Agent
| Attribute | Detail |
|---|---|
| **Role** | Discover relevant content across the healthcare robotics landscape |
| **Trigger** | Daily schedule (morning) or manual refresh |
| **Tools** | Tavily web search API, Anthropic tool use |
| **Inputs** | Predefined search query templates per event type and sub-segment |
| **Outputs** | List of raw results: `{title, url, snippet, source, date, query_tag}` |
| **Logic** | Runs N queries across event types and sub-segments; deduplicates URLs against the database before passing downstream |

### Agent 2 — Extraction Agent
| Attribute | Detail |
|---|---|
| **Role** | Parse raw results into structured, validated data records |
| **Tools** | Anthropic tool use, Pydantic models |
| **Inputs** | Raw result list from Search Agent |
| **Outputs** | Structured records: `{company, product, event_type, sub_segment, date, summary, source_url, funding_amount, fda_status}` |
| **Logic** | For each result, prompts Claude to extract structured fields; validates against Pydantic schema; flags records that fail validation for review |

### Agent 3 — Synthesis Agent
| Attribute | Detail |
|---|---|
| **Role** | Generate the written briefing and flag notable events |
| **Tools** | Anthropic tool use |
| **Inputs** | All structured records from current pipeline run |
| **Outputs** | `{briefing_text, notable_events[]}` |
| **Logic** | Identifies high-signal events using rule-based flagging (funding threshold, event type priority) + LLM scoring; generates narrative briefing covering top stories and trends |

### Handoff Summary
```
Search Agent → [raw results] → Extraction Agent → [structured records] → Synthesis Agent → [briefing + alerts] → Dashboard + Email
```

---

## 5. Data Model

All data persisted in SQLite.

### `events` table
| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `run_id` | TEXT | Links to pipeline run |
| `company` | TEXT | Company name |
| `product` | TEXT | Product or technology name |
| `event_type` | TEXT | funding / fda / launch / research / news |
| `sub_segment` | TEXT | surgical / rehabilitation / diagnostics / other |
| `date` | DATE | Date of event |
| `summary` | TEXT | One-sentence summary |
| `source_url` | TEXT | Original source link |
| `funding_amount` | INTEGER | In USD, nullable |
| `fda_status` | TEXT | Cleared / Approved / De Novo, nullable |
| `is_notable` | BOOLEAN | Flagged as high-signal |
| `created_at` | TIMESTAMP | Record creation time |

### `pipeline_runs` table
| Column | Type | Description |
|---|---|---|
| `id` | TEXT PK | UUID |
| `run_date` | DATE | Date of run |
| `triggered_by` | TEXT | schedule / manual |
| `status` | TEXT | running / complete / failed |
| `briefing_text` | TEXT | Full generated briefing |
| `completed_at` | TIMESTAMP | Completion timestamp |

---

## 6. Tech Stack

| Layer | Tool | Rationale |
|---|---|---|
| Agent framework | Anthropic Python SDK | Native tool use support; full control over agent logic |
| Web search | Tavily API | Designed for LLM pipelines; returns clean, structured results |
| Data validation | Pydantic | Enforces schema at extraction layer; catches malformed records early |
| Database | SQLite | Zero-config persistence; sufficient for v1 single-user scope |
| Dashboard | Streamlit | Python-native; fast to build; easy to demo |
| Email | SendGrid | Reliable delivery; simple Python integration |
| Scheduling | APScheduler | Lightweight Python scheduler; runs inside the app process |
| Environment | Python 3.11+, `.env` for secrets | Standard, portable setup |

---

## 7. Out of Scope (v1)

The following are explicitly excluded from v1 to maintain a focused, shippable scope:

- Multi-user authentication or access controls
- Real-time streaming of pipeline progress
- Integration with paid data sources (e.g. PitchBook, Bloomberg)
- Mobile-optimized dashboard
- Custom alert thresholds per user
- Export to PDF or Excel
- Natural language querying of the database ("show me all FDA clearances in Q1")

---

## 8. Success Metrics

| Metric | Target |
|---|---|
| Pipeline completes daily without manual intervention | 100% of scheduled runs |
| New relevant events surfaced per run | 10–30 structured records |
| Notable events flagged per run | 2–5 high-signal alerts |
| Email digest delivered within 30 min of scheduled run time | >95% of runs |
| Dashboard loads in under 3 seconds | >95% of page loads |
| Duplicate events in database | <5% of total records |

---

## 9. Roadmap

Features considered for future versions, in rough priority order:

1. **Natural language search** — query the database conversationally ("show me all surgical robotics funding rounds over $100M in 2025")
2. **Company profiles** — dedicated pages per company with full event history, funding timeline, and product list
3. **PitchBook / Crunchbase integration** — enrich funding records with verified financial data
4. **Custom watchlist** — user defines specific companies or keywords to monitor more closely
5. **Competitive landscape view** — map companies by sub-segment and funding stage
6. **Slack integration** — deliver notable events alerts to a Slack channel in addition to email
7. **Multi-user support** — authentication, per-user watchlists and digest preferences
8. **Export** — download filtered table views as CSV or PDF report

---

## 10. Open Questions

- What is the optimal number of search queries per run to balance coverage vs. API cost?
- Should the Synthesis Agent re-read previous briefings to maintain narrative continuity across days?
- What funding threshold should trigger a "notable event" flag? (Default assumption: $50M)
- Should failed pipeline runs retry automatically or alert the user?
