import json
import logging
from datetime import date
from pipeline.llm import call_tools
from pipeline.models import ExtractedEvent
from pipeline.text_utils import strip_index_refs
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
                "description": "The daily research brief in Markdown, following the required structure",
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


PREVIOUS_BRIEFING_CHARS = 2500
CONTEXT_DAYS = 90
CONTEXT_EVENT_LIMIT = 150


def _previous_briefings_text(previous: list[dict]) -> str:
    if not previous:
        return ""
    parts = [
        f"--- Briefing of {b['run_date']} ---\n{b['briefing_text'][:PREVIOUS_BRIEFING_CHARS]}"
        for b in previous
    ]
    return (
        "\n\nFor continuity, here are the most recent previous briefings (newest first). When a story "
        "develops, say so (e.g. \"following up on…\"); do not repeat old news as if it were new.\n\n"
        + "\n\n".join(parts)
    )


def _context_events_text(context_events: list[dict]) -> str:
    """Earlier tracked events, one line each, so the brief can explain today's news in context."""
    if not context_events:
        return ""
    lines = []
    for e in context_events[:CONTEXT_EVENT_LIMIT]:
        extras = [x for x in [
            f"${e['funding_amount']:,.0f}" if e.get("funding_amount") else None,
            e.get("regulatory_status"),
            e.get("source_type"),
        ] if x]
        detail = " (" + "; ".join(extras) + ")" if extras else ""
        lines.append(
            f"- {e.get('date') or 'undated'} · {e.get('company') or 'Unknown'} · {e.get('event_type')}: "
            f"{e.get('summary', '')}{detail}"
        )
    return (
        f"\n\nBackground: events tracked in this industry over the previous {CONTEXT_DAYS} days (newest first). "
        "Use them for context, trend lines, trackers and comparisons, never as today's news:\n"
        + "\n".join(lines)
    )


def run_synthesis_agent(events: list[ExtractedEvent], profile: IndustryProfile,
                        previous_briefings: list[dict] | None = None,
                        context_events: list[dict] | None = None) -> tuple[str, list[int]]:
    """Returns (briefing_text, list_of_notable_indices).

    `previous_briefings`: recent runs' {run_date, briefing_text}, newest first.
    `context_events`: earlier stored events for this industry (dicts from the events table), newest first.
    """
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
    prompt = f"""You are the research analyst for {profile.label}, an intelligence platform tracking the {profile.name} industry: {profile.description}

Today is {today}. Below are {len(events)} structured events discovered in the latest pipeline run (roughly the last day):

{events_json}

Your tasks:
1. Write today's research brief: a 1-2 page market note in the style of an equity research morning note or a business newspaper's industry brief, not a list of news items. Pick the few most important stories from this run, explain what they mean for the industry, and connect them to the earlier events in the background section. Aim for 900-1,300 words.

   Use exactly this Markdown structure:

   # <Headline: one sharp, specific sentence on the day's most important development>
   *<Dek: one sentence tying together the two or three biggest themes>*

   ## The bottom line
   3-4 bullets, each "- **<takeaway in a few words>.** <one sentence of explanation>". A reader who stops here should know what changed and why it matters.

   ## By the numbers
   3-4 bullets, each "- **<figure>** — <what it is>". Use only figures that appear in the events (funding amounts, counts of clearances, trial sizes); never estimate or invent one. Prefer figures from official filings, research records or established news outlets; skip a figure whose only source is social media, a blog or an unreviewed upload.

   Then 3-5 analysis sections, each headed "## <Theme>: <headline for the section>" (themes such as The lead, Deals and capital, Regulatory, Products and launches, Clinical evidence, Research, Competitive landscape). The first is always "## The lead: ...". Each section is 1-3 short paragraphs of analysis, not a recap: what happened, why it matters, who gains or loses, and how it fits the trend in the background events. End a section with "**Why it matters:** <one sentence>" when the implication is not already obvious.

   ## What to watch
   A numbered list of 3-5 companies or developments, each "1. **<name>** — <the specific next signal to look for>". Favor the user's watchlist ({watchlist}) when they have news.

   Cite sources inline as Markdown links on the claim they support, e.g. "cleared 510(k) on September 10 ([openFDA](https://...))", using each event's source_url. Cite every figure and every regulatory action; do not cite every sentence.

2. Identify additional notable events beyond the rule-flagged ones ({_describe_rules(profile)}).
   Be selective: flag only the handful of events (typically under 10) that represent significant market signals such as M&A, breakthrough products, landmark research or major partnerships.
   Return their index numbers.

Source trust: events with source_type openfda or sec_form_d come from official filings and are authoritative; pubmed, arxiv and clinicaltrials are primary research records; tavily results are secondary (web/news). When a news report conflicts with a primary source (e.g. a different funding amount or clearance date), use the primary source's figure and say so briefly.

Submit both via the submit_briefing tool.

Write for a {profile.briefing_audience} audience: specific, analytical and plain-spoken, with company names, amounts and dates. Every claim must come from the events below or the background events; do not add outside facts. If this run is thin, write a shorter brief rather than padding it. The index numbers are only for additional_notable_indices: never mention them in briefing_text.{_context_events_text(context_events or [])}{_previous_briefings_text(previous_briefings or [])}"""

    try:
        (parsed,) = call_tools([{"prompt": prompt, "tool": BRIEFING_TOOL, "max_tokens": 8000}])
        if isinstance(parsed, Exception):
            raise parsed
        briefing_text = strip_index_refs(parsed["briefing_text"])
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
