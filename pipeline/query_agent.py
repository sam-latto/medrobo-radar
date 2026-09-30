"""Answer plain-English questions about the collected data with a read-only tool loop."""
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date

from config import MODEL
from database import queries
from pipeline.llm import client, thinking_param
from profiles.loader import load_profiles

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 8
CITATION_RE = re.compile(r"\[E(\d+)\]")

_FILTER_PROPERTIES = {
    "industry_id": {
        "type": "string",
        "description": "Industry profile id. Omit to use the industry the user is viewing; 'all' searches every industry.",
    },
    "segment": {"type": "string", "description": "Segment key, e.g. surgical"},
    "event_type": {"type": "string", "description": "Event type key, e.g. funding"},
    "company": {"type": "string", "description": "Company name (substring match)"},
    "date_from": {"type": "string", "description": "Earliest event date, YYYY-MM-DD"},
    "date_to": {"type": "string", "description": "Latest event date, YYYY-MM-DD"},
    "min_funding": {"type": "integer", "description": "Minimum funding/deal amount in USD"},
    "source_type": {
        "type": "string",
        "description": "tavily (web/news), openfda, sec_form_d, pubmed, arxiv, clinicaltrials",
    },
    "notable_only": {"type": "boolean", "description": "Only events flagged notable"},
    "text": {"type": "string", "description": "Keyword matched against summary, company, product"},
    "undated_only": {
        "type": "boolean",
        "description": "Only events whose date is unknown (use with no date filters to inspect them)",
    },
}

BASE_TOOLS = [
    {
        "name": "search_events",
        "description": (
            "Search structured events (funding, regulatory, launches, research, deals, news). "
            "Returns events newest first, each with an id to cite as [E<id>]. Events without a "
            "known date are excluded whenever a date filter is used."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                **_FILTER_PROPERTIES,
                "limit": {"type": "integer", "description": "Max events to return (default 25, max 100)"},
            },
        },
    },
    {
        "name": "aggregate_events",
        "description": (
            "Count events and sum disclosed funding, grouped by a dimension. Use for comparisons, "
            "rankings and trends. Each group includes sample event ids for citation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "group_by": {"type": "string", "enum": sorted(queries.GROUP_BY_SQL)},
                **_FILTER_PROPERTIES,
                "limit": {"type": "integer", "description": "Max groups (default 50)"},
            },
            "required": ["group_by"],
        },
    },
    {
        "name": "get_briefings",
        "description": "Get the narrative daily briefings written after each pipeline run.",
        "input_schema": {
            "type": "object",
            "properties": {
                "industry_id": _FILTER_PROPERTIES["industry_id"],
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                "limit": {"type": "integer", "description": "Max briefings (default 5)"},
            },
        },
    },
]

# Extra tools registered by other modules (e.g. get_company): name -> (definition, handler)
EXTRA_TOOLS: dict[str, tuple[dict, callable]] = {}


@dataclass
class AskResult:
    answer: str                       # Markdown with [E123] citations turned into links
    cited_events: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    hit_iteration_cap: bool = False


def _resolve_industry(value: str | None, default_industry_id: str | None) -> str | None:
    if value is None or value == "":
        return default_industry_id
    return None if value.lower() == "all" else value


def _compact_event(e: dict) -> dict:
    return {k: v for k, v in e.items() if v not in (None, "", 0) or k == "is_notable"}


_FILTER_KEYS = set(_FILTER_PROPERTIES) - {"industry_id"}


def run_tool(name: str, args: dict, default_industry_id: str | None) -> dict:
    """Execute one tool call. All tools are read-only and parameterized."""
    industry_id = _resolve_industry(args.get("industry_id"), default_industry_id)
    filters = {k: v for k, v in args.items() if k in _FILTER_KEYS and v not in (None, "")}

    if name == "search_events":
        limit = args.get("limit", 25)
        events = queries.search_events(industry_id=industry_id, limit=limit, **filters)
        total = queries.count_matching_events(industry_id=industry_id, **filters)
        out = {"total_matching": total, "returned": len(events), "events": [_compact_event(e) for e in events]}
        if filters.get("date_from") or filters.get("date_to"):
            undated_filters = {k: v for k, v in filters.items() if k not in ("date_from", "date_to")}
            undated_filters["undated_only"] = True
            out["undated_matching_other_filters"] = queries.count_matching_events(
                industry_id=industry_id, **undated_filters)
        return out
    if name == "aggregate_events":
        rows = queries.aggregate_events(
            group_by=args["group_by"], industry_id=industry_id, limit=args.get("limit", 50), **filters
        )
        return {"groups": rows}
    if name == "get_briefings":
        rows = queries.get_briefings(
            industry_id=industry_id, date_from=args.get("date_from"),
            date_to=args.get("date_to"), limit=args.get("limit", 5),
        )
        return {"briefings": rows}
    if name in EXTRA_TOOLS:
        return EXTRA_TOOLS[name][1](args, industry_id)
    raise ValueError(f"Unknown tool: {name}")


def _tools() -> list[dict]:
    return BASE_TOOLS + [definition for definition, _ in EXTRA_TOOLS.values()]


def _system_prompt(default_industry_id: str | None) -> str:
    lines = []
    for p in load_profiles():
        lines.append(
            f"- {p.id} ({p.label}{'' if p.active else ', inactive'}): segments={p.segment_choices}; "
            f"event_types={p.event_types}"
        )
    viewing = default_industry_id or "all industries"
    return f"""You answer questions about an industry-intelligence database built by an automated research pipeline. Today is {date.today().isoformat()}.

Industries tracked:
{chr(10).join(lines)}

The user is currently viewing: {viewing}. Tools default to that scope; pass industry_id="all" (or another id) only when the question clearly calls for it.

How to answer:
- Use the tools to look things up; never guess facts that are not in tool results. Make several calls if needed (for example aggregate first, then search for details).
- Cite every factual claim with the event ids it comes from, formatted exactly like [E123] (one id per bracket; list several as [E12][E40]).
- Events with source_type openfda or sec_form_d come from primary filings and are authoritative; tavily (web/news) is secondary. When they conflict, prefer the primary source and say so.
- Some events have an unknown date, and date filters exclude them. When search_events reports undated_matching_other_filters > 0, look at those events (undated_only=true) and include any that plausibly fall in the period, clearly labelled "date unknown".
- The same real-world event can appear in several articles. De-duplicate when counting or summing (e.g. one funding round reported twice counts once) and say when totals are approximate.
- If the data does not contain the answer, say so plainly and mention what is covered.
- Be concise: lead with the answer, then supporting detail. Use Markdown tables for comparisons."""


def _link_citations(text: str, events_by_id: dict[int, dict]) -> str:
    def repl(m: re.Match) -> str:
        e = events_by_id.get(int(m.group(1)))
        return f"[[E{m.group(1)}]]({e['source_url']})" if e and e.get("source_url") else m.group(0)
    return CITATION_RE.sub(repl, text)


def ask(question: str, history: list[dict] | None = None,
        default_industry_id: str | None = None) -> tuple[AskResult, list[dict]]:
    """Answer `question`. `history` is the prior API message list (returned from the
    previous call); the updated history is returned for the next turn."""
    messages = list(history or []) + [{"role": "user", "content": question}]
    system = _system_prompt(default_industry_id)
    tools = _tools()
    tool_calls: list[dict] = []
    hit_cap = False
    extra = {}
    thinking = thinking_param()
    if thinking:
        extra["thinking"] = thinking

    for iteration in range(MAX_TOOL_ITERATIONS + 1):
        final_round = iteration == MAX_TOOL_ITERATIONS
        response = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=system,
            tools=tools,
            tool_choice={"type": "none"} if final_round else {"type": "auto"},
            messages=messages,
            **extra,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            hit_cap = final_round
            break

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            try:
                output = run_tool(block.name, dict(block.input), default_industry_id)
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(output, default=str)})
                tool_calls.append({"tool": block.name, "input": block.input})
            except Exception as e:
                logger.warning(f"Ask tool {block.name} failed: {e}")
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": f"Error: {e}", "is_error": True})
                tool_calls.append({"tool": block.name, "input": block.input, "error": str(e)})
        messages.append({"role": "user", "content": results})

    answer = "\n".join(b.text for b in response.content if b.type == "text").strip()
    if not answer:
        answer = "I couldn't produce an answer for that question."
    cited_ids = sorted({int(i) for i in CITATION_RE.findall(answer)})
    cited = queries.get_events_by_ids(cited_ids)
    events_by_id = {e["id"]: e for e in cited}
    result = AskResult(
        answer=_link_citations(answer, events_by_id),
        cited_events=[events_by_id[i] for i in cited_ids if i in events_by_id],
        tool_calls=tool_calls,
        hit_iteration_cap=hit_cap,
    )
    return result, messages
