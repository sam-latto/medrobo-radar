import logging
import anthropic
from config import ANTHROPIC_API_KEY, MODEL
from pipeline.models import RawResult, ExtractedEvent

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

EXTRACT_TOOL = {
    "name": "extract_event",
    "description": "Extract structured event data from a healthcare robotics news article.",
    "input_schema": {
        "type": "object",
        "properties": {
            "company": {
                "type": "string",
                "description": "Name of the primary company involved",
            },
            "product": {
                "type": "string",
                "description": "Name of the product, technology, or robot if mentioned",
            },
            "event_type": {
                "type": "string",
                "enum": ["funding", "fda", "launch", "research", "news"],
                "description": "Type of event",
            },
            "sub_segment": {
                "type": "string",
                "enum": ["surgical", "rehabilitation", "diagnostics", "exoskeletons", "ai_assisted", "other"],
                "description": "Healthcare robotics sub-segment",
            },
            "date": {
                "type": "string",
                "description": "Event date in YYYY-MM-DD format, or best estimate",
            },
            "summary": {
                "type": "string",
                "description": "One-sentence summary of the event",
            },
            "funding_amount": {
                "type": "integer",
                "description": "Funding amount in USD (e.g. 50000000 for $50M), only if applicable",
            },
            "fda_status": {
                "type": "string",
                "description": "FDA status string: 'Cleared', 'Approved', or 'De Novo', only if applicable",
            },
        },
        "required": ["event_type", "sub_segment", "summary"],
    },
}


def run_extraction_agent(raw_results: list[RawResult]) -> list[ExtractedEvent]:
    extracted: list[ExtractedEvent] = []

    for result in raw_results:
        hint_event, hint_segment = result.query_tag.split(":", 1)
        prompt = f"""You are analyzing a healthcare robotics news article. Extract structured information from it.

Title: {result.title}
Source: {result.source}
Published: {result.date or 'Unknown'}
Content: {result.snippet}

Search context: This result was found while searching for '{hint_event}' events in the '{hint_segment}' sub-segment.

Use the extract_event tool to return the structured data."""

        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=1024,
                tools=[EXTRACT_TOOL],
                tool_choice={"type": "tool", "name": "extract_event"},
                messages=[{"role": "user", "content": prompt}],
            )

            tool_use = next(
                (b for b in response.content if b.type == "tool_use"), None
            )
            if tool_use is None:
                logger.warning(f"No tool use in response for {result.url}")
                continue

            data = tool_use.input
            data["source_url"] = result.url
            if "date" not in data or not data.get("date"):
                data["date"] = result.date

            event = ExtractedEvent(**data)
            extracted.append(event)

        except Exception as e:
            logger.error(f"Extraction failed for {result.url}: {e}")

    logger.info(f"Extraction agent produced {len(extracted)} structured records")
    return extracted
