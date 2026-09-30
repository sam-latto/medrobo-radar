import logging
from config import EXTRACTION_BATCH_SIZE
from pipeline.llm import call_tool
from pipeline.models import RawResult, ExtractedEvent
from profiles.loader import IndustryProfile

logger = logging.getLogger(__name__)


def build_extract_tool(profile: IndustryProfile) -> dict:
    event_properties = {
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
            "enum": profile.event_types,
            "description": "Type of event",
        },
        "segment": {
            "type": "string",
            "enum": profile.segment_choices,
            "description": f"{profile.name} segment",
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
            "description": "Amount raised or deal value in USD (e.g. 50000000 for $50M), only if applicable",
        },
        "regulatory_status": {
            "type": "string",
            "description": "Regulatory milestone, e.g. 'FDA 510(k) Cleared', 'FDA De Novo', 'CE Mark', only if applicable",
        },
    }
    return {
        "name": "record_events",
        "description": f"Record one structured event per {profile.name} search result.",
        "input_schema": {
            "type": "object",
            "properties": {
                "events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": event_properties,
                        "required": ["result_id", "event_type", "segment", "summary"],
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
        f"Source: {result.source} ({result.source_type})\n"
        f"Published: {result.date or 'Unknown'}\n"
        f"Search context: found while searching for '{hint_event}' events in the '{hint_segment}' segment\n"
        f"Content: {result.snippet}"
    )


def _extract_batch(batch: list[RawResult], profile: IndustryProfile) -> list[ExtractedEvent]:
    """One Claude call for a batch of results. Raises on API/tool failure."""
    results_text = "\n\n---\n\n".join(_format_result(i, r) for i, r in enumerate(batch))
    prompt = f"""You are analyzing {len(batch)} search results for an intelligence platform tracking the {profile.name} industry ({profile.description}).

Extract one structured event per result.

{results_text}

Call the record_events tool with one entry per result, using its [id] as result_id."""

    data = call_tool(prompt, build_extract_tool(profile), max_tokens=8192)

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
        item["source_type"] = result.source_type
        item["industry_id"] = profile.id
        if not item.get("date"):
            item["date"] = result.date
        try:
            events.append(ExtractedEvent.model_validate(item, context={"profile": profile}))
        except Exception as e:
            logger.error(f"Invalid extracted event for {result.url}: {e}")
    return events


def run_extraction_agent(raw_results: list[RawResult], profile: IndustryProfile) -> list[ExtractedEvent]:
    extracted: list[ExtractedEvent] = []
    api_calls = 0

    for start in range(0, len(raw_results), EXTRACTION_BATCH_SIZE):
        batch = raw_results[start : start + EXTRACTION_BATCH_SIZE]
        try:
            api_calls += 1
            extracted.extend(_extract_batch(batch, profile))
        except Exception as e:
            # Isolate failures: retry each item of the failed batch on its own
            logger.error(f"Extraction batch starting at {start} failed ({e}); retrying items individually")
            for result in batch:
                try:
                    api_calls += 1
                    extracted.extend(_extract_batch([result], profile))
                except Exception as item_error:
                    logger.error(f"Extraction failed for {result.url}: {item_error}")

    logger.info(
        f"[{profile.id}] Extraction agent produced {len(extracted)} structured records "
        f"from {len(raw_results)} results in {api_calls} API calls"
    )
    return extracted
