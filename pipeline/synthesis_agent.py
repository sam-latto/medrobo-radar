import json
import logging
from datetime import date
from pipeline.llm import call_tool
from pipeline.models import ExtractedEvent
from profiles.loader import IndustryProfile

logger = logging.getLogger(__name__)

BRIEFING_TOOL = {
    "name": "submit_briefing",
    "description": "Submit the daily briefing and the indices of additional notable events.",
    "input_schema": {
        "type": "object",
        "properties": {
            "briefing_text": {
                "type": "string",
                "description": "Narrative daily briefing in Markdown, 3-5 paragraphs",
            },
            "additional_notable_indices": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Indices of notable events beyond the rule-flagged ones",
            },
        },
        "required": ["briefing_text", "additional_notable_indices"],
    },
}


def is_rule_notable(event: ExtractedEvent, profile: IndustryProfile) -> bool:
    rules = profile.notable_rules
    if event.event_type in rules.always_notable_event_types:
        return True
    if event.funding_amount and event.funding_amount >= rules.funding_threshold_usd:
        return True
    return False


def _describe_rules(profile: IndustryProfile) -> str:
    rules = profile.notable_rules
    parts = [f"funding of ${rules.funding_threshold_usd:,.0f} or more"]
    if rules.always_notable_event_types:
        parts.append("all " + ", ".join(rules.always_notable_event_types) + " events")
    return "; ".join(parts)


def run_synthesis_agent(events: list[ExtractedEvent], profile: IndustryProfile) -> tuple[str, list[int]]:
    """Returns (briefing_text, list_of_notable_indices)."""
    if not events:
        return "No new events were found in this pipeline run.", []

    rule_notable_indices = [i for i, e in enumerate(events) if is_rule_notable(e, profile)]

    events_json = json.dumps(
        [
            {
                "index": i,
                "company": e.company,
                "product": e.product,
                "event_type": e.event_type,
                "segment": e.segment,
                "date": e.date,
                "summary": e.summary,
                "funding_amount": e.funding_amount,
                "regulatory_status": e.regulatory_status,
                "source_type": e.source_type,
                "source_url": e.source_url,
            }
            for i, e in enumerate(events)
        ],
        indent=2,
    )

    watchlist = ", ".join(profile.watchlist) if profile.watchlist else "none specified"
    today = date.today().strftime("%B %d, %Y")
    prompt = f"""You are the Synthesis Agent for {profile.label}, an intelligence platform tracking the {profile.name} industry: {profile.description}

Today is {today}. Below are {len(events)} structured events discovered in the latest pipeline run:

{events_json}

Your tasks:
1. Write a narrative daily briefing (3-5 paragraphs) covering:
   - Top stories from this run
   - Emerging trends across segments ({", ".join(profile.segments)})
   - Companies to watch (the user's watchlist: {watchlist})

2. Identify additional notable events beyond the rule-flagged ones ({_describe_rules(profile)}).
   Be selective: flag only the handful of events (typically under 10) that represent significant market signals such as M&A, breakthrough products, landmark research or major partnerships.
   Return their index numbers.

Source trust: events with source_type openfda or sec_form_d come from official filings and are authoritative; pubmed, arxiv and clinicaltrials are primary research records; tavily results are secondary (web/news). When a news report conflicts with a primary source (e.g. a different funding amount or clearance date), use the primary source's figure and say so briefly.

Submit both via the submit_briefing tool.

Write for a {profile.briefing_audience} audience. Be specific, cite companies and amounts. Do not pad the briefing."""

    try:
        parsed = call_tool(prompt, BRIEFING_TOOL, max_tokens=4096)
        briefing_text = parsed["briefing_text"]
        additional = [
            i for i in parsed.get("additional_notable_indices", [])
            if isinstance(i, int) and 0 <= i < len(events)
        ]
        notable_indices = sorted(set(rule_notable_indices + additional))
        logger.info(f"[{profile.id}] Synthesis agent: {len(notable_indices)} notable events flagged")
        return briefing_text, notable_indices
    except Exception as e:
        logger.error(f"Synthesis agent failed: {e}")
        # Fallback: plain listing
        lines = [f"**{profile.label} — {today}**\n"]
        for e in events[:10]:
            lines.append(f"- [{e.event_type.upper()}] {e.company or 'Unknown'}: {e.summary}")
        return "\n".join(lines), rule_notable_indices
