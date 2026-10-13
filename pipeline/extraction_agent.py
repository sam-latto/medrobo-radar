import logging
from config import EXTRACTION_BATCH_SIZE
from config import EXTRACTION_MODEL
from pipeline.llm import call_tools
from pipeline.models import RawResult, ExtractedEvent
from profiles.loader import IndustryProfile

logger = logging.getLogger(__name__)


def build_extract_tool(profile: IndustryProfile) -> dict:
    event_properties = {
        "result_id": {
            "type": "integer",
            "description": "The [id] of the search result this event was extracted from",
        },
        "relevant": {
            "type": "boolean",
            "description": (
                f"false if the result is not really about the {profile.name} industry "
                "(off-topic article, unrelated company) or isn't a single announcement (roundup, "
                "newsletter, listicle, listing page); such results are discarded"
            ),
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
            "description": ("Amount raised or acquisition/merger price in USD (e.g. 50000000 for $50M); "
                            "never contract, revenue or valuation figures"),
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
                        "required": ["result_id", "relevant", "event_type", "segment", "summary"],
                    },
                }
            },
            "required": ["events"],
        },
    }


PREFILL_FIELDS = ("company", "product", "event_type", "date", "funding_amount", "regulatory_status")


def _format_result(result_id: int, result: RawResult) -> str:
    hint_event, hint_segment = result.query_tag.split(":", 1)
    lines = [
        f"[id={result_id}]",
        f"Title: {result.title}",
        f"Source: {result.source} ({result.source_type})",
        f"Published: {result.date or 'Unknown'}",
        f"Search context: found while searching for '{hint_event}' events in the '{hint_segment}' segment",
    ]
    facts = {k: v for k, v in result.prefilled.items() if v is not None}
    if facts:
        lines.append(f"Authoritative facts from the primary source (use as-is): {facts}")
    lines.append(f"Content: {result.snippet}")
    return "\n".join(lines)


def _merge_prefilled(item: dict, result: RawResult) -> dict:
    for key in PREFILL_FIELDS:
        if result.prefilled.get(key) is not None:
            item[key] = result.prefilled[key]
    return item


def _event_from_prefilled(result: RawResult) -> dict:
    """Fallback when the model skipped a primary-source result it did not reject."""
    return _merge_prefilled({"summary": result.title, "segment": "other", "event_type": "news"}, result)


def _extract_request(batch: list[RawResult], profile: IndustryProfile) -> dict:
    """The Claude call for one group of results."""
    results_text = "\n\n---\n\n".join(_format_result(i, r) for i, r in enumerate(batch))
    prompt = f"""You are analyzing {len(batch)} search results for an intelligence platform tracking the {profile.name} industry ({profile.description}).

Extract one structured event per result. Where a result lists authoritative facts from a primary source (FDA, SEC, research databases), keep those facts exactly; classify its segment and write the summary from them. Mark results that are not really about this industry as relevant=false.

Rules:
- funding_amount is only money raised (equity rounds, grants, debt) or the price of an acquisition or merger. Customer contracts, bookings, revenue, orders, valuations and market-size estimates are not funding: leave funding_amount empty and don't use the funding event type for them.
- Each event is one announcement. Mark roundups, newsletters, "top 10" lists, index or listing pages, event calendars and job posts as relevant=false: they summarize many stories instead of reporting one.
- When several results report the same announcement, record each, using the same company name, amount and date in all of them.

{results_text}

Call the record_events tool with one entry per result, using its [id] as result_id."""

    return {"prompt": prompt, "tool": build_extract_tool(profile), "max_tokens": 8192, "model": EXTRACTION_MODEL}


def _parse_events(data: dict, batch: list[RawResult], profile: IndustryProfile) -> list[ExtractedEvent]:
    """Events from the tool input returned for one group of results."""
    items: list[tuple[RawResult, dict]] = []
    seen_ids: set[int] = set()
    dropped = 0
    for item in data.get("events", []):
        result_id = item.pop("result_id", None)
        if not isinstance(result_id, int) or not 0 <= result_id < len(batch) or result_id in seen_ids:
            logger.warning(f"Extraction returned invalid or duplicate result_id: {result_id}")
            continue
        seen_ids.add(result_id)
        if item.pop("relevant", True) is False:
            dropped += 1
            continue
        items.append((batch[result_id], _merge_prefilled(item, batch[result_id])))

    for result_id, result in enumerate(batch):
        if result_id not in seen_ids and result.prefilled:
            items.append((result, _event_from_prefilled(result)))
    if dropped:
        logger.info(f"Extraction discarded {dropped} off-topic result(s)")

    events: list[ExtractedEvent] = []
    for result, item in items:
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
    """Extract events in groups of EXTRACTION_BATCH_SIZE. All groups go out together (as one
    Message Batch when the worker turns batch mode on); a group that fails is retried one
    result at a time, so one bad result can't lose the others."""
    groups = [raw_results[i:i + EXTRACTION_BATCH_SIZE] for i in range(0, len(raw_results), EXTRACTION_BATCH_SIZE)]
    extracted: list[ExtractedEvent] = []
    retry: list[RawResult] = []
    for group, data in zip(groups, call_tools([_extract_request(g, profile) for g in groups])):
        try:
            if isinstance(data, Exception):
                raise data
            extracted.extend(_parse_events(data, group, profile))
        except Exception as e:
            logger.error(f"Extraction of a group of {len(group)} failed ({e}); retrying its results individually")
            retry.extend(group)
    for result, data in zip(retry, call_tools([_extract_request([r], profile) for r in retry])):
        try:
            if isinstance(data, Exception):
                raise data
            extracted.extend(_parse_events(data, [result], profile))
        except Exception as e:
            logger.error(f"Extraction failed for {result.url}: {e}")

    logger.info(
        f"[{profile.id}] Extraction agent produced {len(extracted)} structured records "
        f"from {len(raw_results)} results in {len(groups) + len(retry)} API calls"
    )
    return extracted
