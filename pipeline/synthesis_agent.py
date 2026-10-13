import json
import logging
from datetime import date
from pipeline.llm import call_tools
from pipeline.models import ExtractedEvent
from pipeline.text_utils import drop_unknown_links, strip_index_refs
from profiles.loader import IndustryProfile

logger = logging.getLogger(__name__)

BRIEFING_TOOL = {
    "name": "submit_briefing",
    "description": "Submit the research brief.",
    "input_schema": {
        "type": "object",
        "properties": {
            "briefing_text": {
                "type": "string",
                "description": "The research brief in Markdown, following the required structure",
            },
        },
        "required": ["briefing_text"],
    },
}


def is_rule_notable(event: ExtractedEvent, profile: IndustryProfile) -> bool:
    rules = profile.notable_rules
    if event.event_type in rules.always_notable_event_types:
        return True
    if event.funding_amount and event.funding_amount >= rules.funding_threshold_usd:
        return True
    return False


PREVIOUS_BRIEFING_CHARS = 2500
# Briefs covering more days than this are written as weekly briefs ("this week", not "today")
DAILY_MAX_DAYS = 2


def _coverage(today: date, previous_brief_date: date | None) -> tuple[int | None, str]:
    """How many days this run's events cover (since the previous brief), and how to say it."""
    if previous_brief_date is None:
        return None, "the latest search, the first for this industry"
    days = max(1, (today - previous_brief_date).days)
    if days <= DAILY_MAX_DAYS:
        return days, "roughly the last day"
    return days, f"the {days} days since the previous brief on {previous_brief_date.strftime('%B %d')}"
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


# What leads the brief, most important first; a profile's briefing_priorities replaces this
DEFAULT_BRIEFING_PRIORITIES = [
    "Large funding rounds, acquisitions and mergers, and regulatory approvals",
    "Commercial traction: major customers, deployments at scale, big-company partnerships, hiring and expansion",
    "Product launches and notable new entrants",
    "Leadership changes and strategy shifts at major companies",
    "Research and clinical trials, only when the result is a clear breakthrough or decisive",
]


def _priorities_text(profile: IndustryProfile) -> str:
    priorities = profile.briefing_priorities or DEFAULT_BRIEFING_PRIORITIES
    return "\n".join(f"   {i}. {p}" for i, p in enumerate(priorities, 1))


KEY_DEALS = 5
KEY_REGULATORY = 5
DEAL_TYPES = {"funding", "partnership_ma"}


def _usd(amount: int) -> str:
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if amount >= size:
            return f"${amount / size:.3g}{suffix}"
    return f"${amount:,}"


def key_items(events: list[ExtractedEvent]) -> list[ExtractedEvent]:
    """The run's largest deals and its regulatory actions: the stories a reader expects covered."""
    deals = sorted((e for e in events if e.event_type in DEAL_TYPES and e.funding_amount and e.company),
                   key=lambda e: e.funding_amount, reverse=True)[:KEY_DEALS]
    regulatory = [e for e in events if e.event_type == "regulatory" and e.company][:KEY_REGULATORY]
    return deals + regulatory


def _key_items_text(items: list[ExtractedEvent]) -> str:
    if not items:
        return ""
    lines = []
    for e in items:
        detail = _usd(e.funding_amount) if e.funding_amount else (e.regulatory_status or e.event_type)
        lines.append(f"- {e.company}: {detail} ({e.event_type}, {e.date or 'date not reported'}) — {e.summary}")
        if e.excerpt:
            lines.append(f'  Source text: "{e.excerpt}"')
    return ("\n\nThe largest deals and the regulatory actions in this run, with the opening of each source "
            "so your analysis of them rests on what the source says. Cover each one in the brief "
            "(a sentence is enough for the smaller ones) unless it's clearly off-topic or old news:\n"
            + "\n".join(lines))


def uncovered(items: list[ExtractedEvent], briefing_text: str) -> list[str]:
    """Companies from `items` the brief never names."""
    text = briefing_text.lower()
    return [e.company for e in items if e.company.lower() not in text]


def run_synthesis_agent(events: list[ExtractedEvent], profile: IndustryProfile,
                        previous_briefings: list[dict] | None = None,
                        context_events: list[dict] | None = None,
                        previous_brief_date: date | None = None) -> tuple[str, list[int]]:
    """Returns (briefing_text, list_of_notable_indices). Notable events are rule-based only
    (profile.notable_rules); the local dashboard still shows them.

    `previous_briefings`: recent runs' {run_date, briefing_text}, newest first.
    `context_events`: earlier stored events for this industry (dicts from the events table), newest first.
    `previous_brief_date`: date of the last brief, so a weekly run is written as a weekly brief.
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
    key = key_items(events)
    today_date = date.today()
    today = today_date.strftime("%B %d, %Y")
    days, covered = _coverage(today_date, previous_brief_date)
    weekly = days is not None and days > DAILY_MAX_DAYS
    period = (f"This is a weekly brief: describe the news as this week's, not today's, and pick the stories "
              f"that matter most across the {days} days." if weekly else "This is a daily brief.")
    prompt = f"""You are the research analyst for {profile.label}, an intelligence platform tracking the {profile.name} industry: {profile.description}

Today is {today}. Below are {len(events)} structured events discovered in the latest pipeline run, covering {covered}. {period}

{events_json}{_key_items_text(key)}

Your task: write the research brief, a 1-2 page industry note in the style of a business newspaper's industry coverage or a consulting firm's industry update, not a list of news items. Pick the few most important stories from this run, explain what they mean for the industry, and connect them to the earlier events in the background section. Aim for 900-1,300 words.

   How to decide what matters most: rank this run's news by the order below, lead with the most important story from the highest category that has real news, and order the analysis sections the same way.
{_priorities_text(profile)}
   Within a category, bigger and more concrete beats smaller and vaguer (a $150M round beats a $10M one; a signed deployment beats a pilot announcement), and a story backed by a filing or research record beats one with only a news report. A neat theme is not a reason to rank a smaller story above a larger one.

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

   Cite sources inline as Markdown links on the claim they support, e.g. "cleared 510(k) on September 10 ([openFDA](https://...))", using each event's source_url exactly as given. Cite every figure and every regulatory action; do not cite every sentence. Links to any other URL are removed before publishing.

Source trust: events with source_type openfda or sec_form_d come from official filings and are authoritative; pubmed, arxiv and clinicaltrials are primary research records; tavily results are secondary (web/news). When a news report conflicts with a primary source (e.g. a different funding amount or clearance date), use the primary source's figure and say so briefly.

Submit it via the submit_briefing tool.

Write for these readers: {profile.briefing_audience}. Be specific, analytical and plain-spoken, with company names, amounts and dates, and briefly explain terms a smart generalist wouldn't know (e.g. De Novo, VLA, Series B extension). Every claim must come from the events below or the background events; do not add outside facts. Many events have no date (null): never guess one, and never use today's date for them; write "this week" or leave the date out. State a date only when the event gives it. If this run is thin, write a shorter brief rather than padding it. Never mention the events' index numbers in the brief.{_context_events_text(context_events or [])}{_previous_briefings_text(previous_briefings or [])}"""

    try:
        (parsed,) = call_tools([{"prompt": prompt, "tool": BRIEFING_TOOL, "max_tokens": 16000}])
        if isinstance(parsed, Exception):
            raise parsed
        allowed_urls = [e.source_url for e in events] + [c.get("source_url") for c in context_events or []]
        briefing_text, dropped = drop_unknown_links(strip_index_refs(parsed["briefing_text"]), allowed_urls)
        missing = uncovered(key, briefing_text)
        if missing:
            logger.warning(f"[{profile.id}] Synthesis agent: brief doesn't mention key items: {', '.join(missing)}")
        if dropped:
            logger.warning(f"[{profile.id}] Synthesis agent: removed {dropped} link(s) to URLs not in the events")
        return briefing_text, rule_notable_indices
    except Exception as e:
        logger.error(f"Synthesis agent failed: {e}")
        # Fallback: plain listing
        lines = [f"**{profile.label} — {today}**\n"]
        for e in events[:10]:
            lines.append(f"- [{e.event_type.upper()}] {e.company or 'Unknown'}: {e.summary}")
        return "\n".join(lines), rule_notable_indices
