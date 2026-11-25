"""One-sentence "what they do" descriptions for companies, written from their own tracked events.

The Companies screen shows what each company does. Nothing collected that, so this step asks the
extraction model (Haiku) for one plain sentence per company, using only the summaries of that
company's events: no outside knowledge, so every description traces back to sourced events.
A company whose events don't say what it does is left without one (the screen then shows its
latest event instead). Runs in the nightly worker for companies still missing a description.
"""
import logging

from config import EXTRACTION_MODEL
from pipeline.llm import call_tools

logger = logging.getLogger(__name__)

GROUP_SIZE = 25          # companies per Claude call
EVENTS_PER_COMPANY = 5   # most recent event summaries used for each
MAX_CHARS = 200
DEFAULT_LIMIT = 200      # companies described per industry per run

DESCRIBE_TOOL = {
    "name": "record_descriptions",
    "description": "Record a one-sentence description of what each company does.",
    "input_schema": {
        "type": "object",
        "properties": {
            "companies": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": "The company's [id]"},
                        "description": {
                            "type": "string",
                            "description": "What the company does or makes, in one sentence of at most 25 words; "
                                           "empty if the events don't say",
                        },
                    },
                    "required": ["id", "description"],
                },
            }
        },
        "required": ["companies"],
    },
}


def build_request(companies: list[dict], industry_name: str) -> dict:
    """One Claude call for a group of {id, name, summaries}."""
    blocks = "\n\n".join(
        f"[id={c['id']}] {c['name']}\n" + "\n".join(f"- {s}" for s in c["summaries"]) for c in companies)
    prompt = f"""Below are companies tracked in the {industry_name} industry, each with summaries of recent news about it.

For each company, write one sentence of at most 25 words saying what the company does or makes (for example "Develops autonomous surgical robots for soft-tissue procedures."). Describe the business, not the news: no amounts, dates or deals.

Use only what the summaries say. If they don't make clear what the company does (for example a university, a fund, or only a passing mention), return an empty description rather than guessing.

{blocks}

Call record_descriptions with one entry per company, using its [id]."""
    return {"prompt": prompt, "tool": DESCRIBE_TOOL, "max_tokens": 4096, "model": EXTRACTION_MODEL}


def parse_descriptions(data: dict, companies: list[dict]) -> dict[int, str]:
    """{company_id: description} for the group's companies, skipping empty or invalid entries."""
    wanted = {c["id"] for c in companies}
    out: dict[int, str] = {}
    for item in (data or {}).get("companies", []):
        cid, text = item.get("id"), " ".join(str(item.get("description") or "").split())
        if cid in wanted and text:
            out[cid] = text if len(text) <= MAX_CHARS else text[:MAX_CHARS].rsplit(" ", 1)[0] + "…"
    return out


def missing_descriptions(conn, industry_id: str, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Companies in the industry with no description but at least one event, most active first."""
    rows = conn.execute(
        """select c.id, c.name,
                  (array_agg(e.summary order by coalesce(e.date, e.created_at::date) desc, e.id desc))
                    [1:%(per)s] as summaries
           from public.companies c
           join public.company_industries ci on ci.company_id = c.id and ci.industry_id = %(industry)s
           join public.events e on e.company_id = c.id
           where c.description is null and e.summary is not null
           group by c.id, c.name
           order by count(e.id) desc, c.name
           limit %(limit)s""",
        {"industry": industry_id, "per": EVENTS_PER_COMPANY, "limit": limit}).fetchall()
    return [dict(r) for r in rows]


def describe_companies(conn, industry_id: str, industry_name: str, limit: int = DEFAULT_LIMIT) -> int:
    """Write descriptions for the industry's companies that lack one. Returns how many were saved.
    A failed group is skipped (logged); the companies are tried again on the next run."""
    companies = missing_descriptions(conn, industry_id, limit)
    if not companies:
        return 0
    groups = [companies[i:i + GROUP_SIZE] for i in range(0, len(companies), GROUP_SIZE)]
    saved = 0
    for group, data in zip(groups, call_tools([build_request(g, industry_name) for g in groups])):
        if isinstance(data, Exception):
            logger.error(f"[{industry_id}] company descriptions failed for a group of {len(group)}: {data}")
            continue
        for cid, text in parse_descriptions(data, group).items():
            # Only fill a gap: never overwrite a description someone set
            saved += conn.execute("update public.companies set description = %s where id = %s and description is null",
                                  (text, cid)).rowcount
    logger.info(f"[{industry_id}] company descriptions: {saved} written for {len(companies)} companies")
    return saved
