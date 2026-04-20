# MedRobo Radar 🤖

> *An autonomous AI agent pipeline for monitoring the healthcare robotics landscape.*

MedRobo Radar continuously scans the web for developments across the healthcare robotics space — funding rounds, FDA clearances, product launches, clinical research, and market news — and delivers structured intelligence through an interactive dashboard and daily email digest.

Built to demonstrate end-to-end AI agent orchestration using the Anthropic API.

---

## Features

- **Multi-agent pipeline** — three sequential agents handle search, extraction, and synthesis
- **Broad coverage** — tracks surgical, rehabilitation, diagnostic, exoskeleton, and AI-assisted robotics
- **Daily scheduled runs** — pipeline executes automatically each morning
- **Interactive dashboard** — filterable company/product tracker, notable events feed, and auto-generated briefing
- **Email digest** — morning summary delivered to your inbox after each run
- **Manual refresh** — trigger an on-demand run anytime from the dashboard

---

## Architecture

```
[Search Agent] → [Extraction Agent] → [Synthesis Agent] → [Dashboard + Email]
```

| Agent | Role |
|---|---|
| **Search Agent** | Queries the web across event types and sub-segments using Tavily |
| **Extraction Agent** | Parses raw results into structured, validated records via Pydantic |
| **Synthesis Agent** | Generates the written briefing and flags high-signal notable events |

---

## Tech Stack

| Layer | Tool |
|---|---|
| Agent framework | Anthropic Python SDK |
| Web search | Tavily API |
| Data validation | Pydantic |
| Database | SQLite |
| Dashboard | Streamlit |
| Email | SendGrid |
| Scheduling | APScheduler |

---

## Getting Started

### Prerequisites
- Python 3.11+
- Anthropic API key
- Tavily API key
- SendGrid API key (for email digest)

### Installation

```bash
git clone https://github.com/yourusername/medrobo-radar.git
cd medrobo-radar
pip install -r requirements.txt
```

### Configuration

Create a `.env` file in the root directory:

```env
ANTHROPIC_API_KEY=your_key_here
TAVILY_API_KEY=your_key_here
SENDGRID_API_KEY=your_key_here
DIGEST_EMAIL=you@example.com
```

### Run

```bash
# Launch the dashboard
streamlit run app.py

# Run the pipeline manually
python pipeline.py --trigger manual
```

---

## Project Structure

```
medrobo-radar/
├── agents/
│   ├── search_agent.py       # Stage 1: web search
│   ├── extraction_agent.py   # Stage 2: structured extraction
│   └── synthesis_agent.py    # Stage 3: briefing + alerts
├── dashboard/
│   └── app.py                # Streamlit dashboard
├── db/
│   └── database.py           # SQLite setup and queries
├── email/
│   └── digest.py             # Email digest sender
├── models/
│   └── schemas.py            # Pydantic data models
├── pipeline.py               # Orchestrator — runs all agents in sequence
├── scheduler.py              # APScheduler daily trigger
├── requirements.txt
├── .env.example
├── PRD.md                    # Full product requirements document
└── README.md
```

---

## Roadmap

See [`PRD.md`](./PRD.md) for the full product requirements document including the v2+ roadmap.

Planned future features include natural language database querying, company profile pages, PitchBook/Crunchbase integration, and a competitive landscape map.

---

## License

MIT
