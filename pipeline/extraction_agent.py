import logging
from config import EXTRACTION_BATCH_SIZE
from pipeline.llm import call_tool
from pipeline.models import RawResult, ExtractedEvent

logger = logging.getLogger(__name__)

EVENT_PROPERTIES = {
    "result_id": {
        "type": "integer",
        "description": "The [id] of the search result this event was extracted from",
    },
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
}

EXTRACT_TOOL = {
    "name": "record_events",
    "description": "Record one structured event per healthcare robotics search result.",
    "input_schema": {
        "type": "object",
        "properties": {
            "events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": EVENT_PROPERTIES,
                    "required": ["result_id", "event_type", "sub_segment", "summary"],
                },
            }
        },
        "required": ["events"],
    },
}


def _format_result(result_id: int, result: RawResult) -> str:
    hint_event, hint_segment = result.query_tag.split(":", 1)
    return (
        f"[id={result_id}]\n"
        f"Title: {result.title}\n"
        f"Source: {result.source}\n"
        f"Published: {result.date or 'Unknown'}\n"
        f"Search context: found while searching for '{hint_event}' events in the '{hint_segment}' sub-segment\n"
        f"Content: {result.snippet}"
    )


def _extract_batch(batch: list[RawResult]) -> list[ExtractedEvent]:
    """One Claude call for a batch of results. Raises on API/tool failure."""
    results_text = "\n\n---\n\n".join(_format_result(i, r) for i, r in enumerate(batch))
    prompt = f"""You are analyzing {len(batch)} healthcare robotics search results. Extract one structured event per result.

{results_text}

Call the record_events tool with one entry per result, using its [id] as result_id."""

    data = call_tool(prompt, EXTRACT_TOOL, max_tokens=8192)

    events: list[ExtractedEvent] = []
    seen_ids: set[int] = set()
    for item in data.get("events", []):
        result_id = item.pop("result_id", None)
        if not isinstance(result_id, int) or not 0 <= result_id < len(batch) or result_id in seen_ids:
            logger.warning(f"Extraction returned invalid or duplicate result_id: {result_id}")
            continue
        seen_ids.add(result_id)
        result = batch[result_id]
        item["source_url"] = result.url
        if not item.get("date"):
            item["date"] = result.date
        try:
            events.append(ExtractedEvent(**item))
        except Exception as e:
            logger.error(f"Invalid extracted event for {result.url}: {e}")
    return events


def run_extraction_agent(raw_results: list[RawResult]) -> list[ExtractedEvent]:
    extracted: list[ExtractedEvent] = []
    api_calls = 0

    for start in range(0, len(raw_results), EXTRACTION_BATCH_SIZE):
        batch = raw_results[start : start + EXTRACTION_BATCH_SIZE]
        try:
            api_calls += 1
            extracted.extend(_extract_batch(batch))
        except Exception as e:
            # Isolate failures: retry each item of the failed batch on its own
            logger.error(f"Extraction batch starting at {start} failed ({e}); retrying items individually")
            for result in batch:
                try:
                    api_calls += 1
                    extracted.extend(_extract_batch([result]))
                except Exception as item_error:
                    logger.error(f"Extraction failed for {result.url}: {item_error}")

    logger.info(
        f"Extraction agent produced {len(extracted)} structured records "
        f"from {len(raw_results)} results in {api_calls} API calls"
    )
    return extracted
