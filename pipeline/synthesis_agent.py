import json
import logging
from datetime import date
from config import NOTABLE_FUNDING_THRESHOLD
from pipeline.llm import call_tool
from pipeline.models import ExtractedEvent

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


def _is_rule_notable(event: ExtractedEvent) -> bool:
    if event.event_type == "fda":
        return True
    if event.event_type == "funding" and event.funding_amount and event.funding_amount >= NOTABLE_FUNDING_THRESHOLD:
        return True
    return False


def run_synthesis_agent(events: list[ExtractedEvent]) -> tuple[str, list[int]]:
    """Returns (briefing_text, list_of_notable_indices)."""
    if not events:
        return "No new events were found in this pipeline run.", []

    rule_notable_indices = [i for i, e in enumerate(events) if _is_rule_notable(e)]

    events_json = json.dumps(
        [
            {
                "index": i,
                "company": e.company,
                "product": e.product,
                "event_type": e.event_type,
                "sub_segment": e.sub_segment,
                "date": e.date,
                "summary": e.summary,
                "funding_amount": e.funding_amount,
                "fda_status": e.fda_status,
                "source_url": e.source_url,
            }
            for i, e in enumerate(events)
        ],
        indent=2,
    )

    today = date.today().strftime("%B %d, %Y")
    prompt = f"""You are the Synthesis Agent for MedRobo Radar, an intelligence platform tracking the healthcare robotics industry.

Today is {today}. Below are {len(events)} structured events discovered in the latest pipeline run:

{events_json}

Your tasks:
1. Write a narrative daily briefing (3-5 paragraphs) covering:
   - Top stories from this run
   - Emerging trends across sub-segments
   - Companies to watch

2. Identify additional notable events beyond the rule-flagged ones (large funding, FDA clearances).
   Flag events that represent significant market signals: M&A, breakthrough products, landmark research, major partnerships.
   Return their index numbers.

Submit both via the submit_briefing tool.

Write for a sophisticated healthcare investor audience. Be specific, cite companies and amounts. Do not pad the briefing."""

    try:
        parsed = call_tool(prompt, BRIEFING_TOOL, max_tokens=4096)
        briefing_text = parsed["briefing_text"]
        additional = [
            i for i in parsed.get("additional_notable_indices", [])
            if isinstance(i, int) and 0 <= i < len(events)
        ]
        notable_indices = sorted(set(rule_notable_indices + additional))
        logger.info(f"Synthesis agent: {len(notable_indices)} notable events flagged")
        return briefing_text, notable_indices
    except Exception as e:
        logger.error(f"Synthesis agent failed: {e}")
        # Fallback: plain listing
        lines = [f"**MedRobo Radar — {today}**\n"]
        for e in events[:10]:
            lines.append(f"- [{e.event_type.upper()}] {e.company or 'Unknown'}: {e.summary}")
        return "\n".join(lines), rule_notable_indices
