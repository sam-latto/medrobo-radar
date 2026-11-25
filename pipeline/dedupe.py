"""Merge events that report the same story.

Search returns the same announcement from several outlets (a funding round on TechCrunch,
BusinessWire and BetaKit), and extraction turns each into its own event. Left alone, the copies
crowd the brief's input and show up twice in the trackers. No Claude call: two events are the
same story when they name the same company and either

- report the same amount (within 2%), within AMOUNT_WINDOW_DAYS, or
- have the same specific event type (funding, launch, regulatory, deal; not news or research) and
  product within TYPE_WINDOW_DAYS (a missing date counts as close).

Of a group, the most authoritative source is kept (filings, then research records, then news),
and fields it lacks (date, amount, product, regulatory status) are filled in from the others.
Events that repeat one already stored in an earlier run are dropped: that's old news.
"""
import logging
from datetime import date

from pipeline.companies import normalize_name
from pipeline.models import ExtractedEvent

logger = logging.getLogger(__name__)

AMOUNT_WINDOW_DAYS = 90
TYPE_WINDOW_DAYS = 7
AMOUNT_TOLERANCE = 0.02
SOURCE_RANK = {"openfda": 0, "sec_form_d": 0, "pubmed": 1, "arxiv": 1, "clinicaltrials": 1}
FILL_FIELDS = ("date", "funding_amount", "product", "regulatory_status", "company", "excerpt")
BROAD_TYPES = {"news", "research", "other"}


def _day(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _days_apart(a, b) -> int | None:
    da, db = _day(a), _day(b)
    return None if da is None or db is None else abs((da - db).days)


def _same_amount(a, b) -> bool:
    if not a or not b:
        return False
    return abs(a - b) <= AMOUNT_TOLERANCE * max(a, b)


def same_story(a: dict, b: dict) -> bool:
    """`a` and `b` are event dicts (ExtractedEvent.model_dump() or stored rows)."""
    company = normalize_name(a.get("company"))
    if not company or company != normalize_name(b.get("company")):
        return False
    gap = _days_apart(a.get("date"), b.get("date"))
    if _same_amount(a.get("funding_amount"), b.get("funding_amount")):
        return gap is None or gap <= AMOUNT_WINDOW_DAYS
    # Catch-all types say too little: two different news items about one company aren't one story
    if a.get("event_type") != b.get("event_type") or a.get("event_type") in BROAD_TYPES:
        return False
    pa, pb = normalize_name(a.get("product")), normalize_name(b.get("product"))
    if pa and pb and pa != pb:
        return False
    return gap is None or gap <= TYPE_WINDOW_DAYS


def _rank(e: ExtractedEvent) -> tuple:
    """Lower is better: authoritative source, then the most complete record."""
    return (SOURCE_RANK.get(e.source_type, 2), e.date is None, e.funding_amount is None, -len(e.summary or ""))


def merge_duplicates(events: list[ExtractedEvent], stored: list[dict] | None = None) -> list[ExtractedEvent]:
    """This run's events with same-story copies merged, minus repeats of `stored` events."""
    stored = stored or []
    groups: list[list[ExtractedEvent]] = []
    for event in events:
        data = event.model_dump()
        for group in groups:
            if same_story(group[0].model_dump(), data):
                group.append(event)
                break
        else:
            groups.append([event])

    merged: list[ExtractedEvent] = []
    repeats = 0
    for group in groups:
        best = min(group, key=_rank)
        fills = {f: next((getattr(e, f) for e in sorted(group, key=_rank) if getattr(e, f)), None)
                 for f in FILL_FIELDS if not getattr(best, f)}
        best = best.model_copy(update={k: v for k, v in fills.items() if v is not None})
        if any(same_story(s, best.model_dump()) for s in stored):
            repeats += 1
            continue
        merged.append(best)

    copies = len(events) - len(groups)
    if copies or repeats:
        logger.info(f"Duplicate merge: {copies} same-story copies merged, {repeats} repeats of earlier events dropped "
                    f"({len(events)} -> {len(merged)} events)")
    return merged
