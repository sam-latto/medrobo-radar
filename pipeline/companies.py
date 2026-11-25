"""Resolve free-text company names on events to rows in the companies table."""
import difflib
import logging
import re

from config import EXTRACTION_MODEL
from database.store import store
from pipeline.llm import call_tool

logger = logging.getLogger(__name__)

_LEGAL_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "llc", "plc",
    "gmbh", "ag", "sa", "sas", "srl", "spa", "bv", "nv", "oy", "ab", "as", "kk", "pte", "pty", "lp", "llp",
}
_NOT_A_COMPANY = {"", "unknown", "various", "multiple", "n a", "na", "none", "not specified", "undisclosed"}

FUZZY_CUTOFF = 0.8


def normalize_name(name: str | None) -> str:
    """'Intuitive Surgical, Inc.' -> 'intuitive surgical'."""
    if not name:
        return ""
    text = name.lower().replace("&", " and ")
    text = re.sub(r"\(.*?\)", " ", text)       # drop parentheticals, e.g. "(NASDAQ: ISRG)"
    text = re.sub(r"[^a-z0-9]+", " ", text)
    words = text.split()
    while words and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    if words and words[0] == "the":
        words = words[1:]
    normalized = " ".join(words)
    return "" if normalized in _NOT_A_COMPANY else normalized


def _fuzzy_candidates(normalized: str, known: dict[str, dict], limit: int = 5) -> list[dict]:
    names = list(known)
    matches = difflib.get_close_matches(normalized, names, n=limit, cutoff=FUZZY_CUTOFF)
    # Also consider word-prefix relations: "medtronic" vs "medtronic surgical robotics"
    for n in names:
        if n not in matches and (n.startswith(normalized + " ") or normalized.startswith(n + " ")):
            matches.append(n)
    return [known[m] for m in matches[:limit]]


MATCH_TOOL = {
    "name": "match_companies",
    "description": "For each new company name, say which known company it is, if any.",
    "input_schema": {
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "description": "Index of the new name"},
                        "company_id": {
                            "type": ["integer", "null"],
                            "description": "id of the same known company, or null if it is a different company",
                        },
                    },
                    "required": ["index", "company_id"],
                },
            }
        },
        "required": ["matches"],
    },
}


def _ask_claude_to_match(ambiguous: list[tuple[str, list[dict]]]) -> dict[int, int | None]:
    """ambiguous: [(new_name, candidate companies)] -> {index: company_id or None}."""
    lines = []
    for i, (name, candidates) in enumerate(ambiguous):
        options = "; ".join(f"id={c['id']}: {c['name']}" for c in candidates)
        lines.append(f"[{i}] \"{name}\" — candidates: {options}")
    prompt = (
        "Company names extracted from news need to be matched to known companies. For each new name, "
        "return the id of the candidate that is the SAME company (same legal entity or an obvious short "
        "form, e.g. 'Intuitive' = 'Intuitive Surgical'). Return null if it is a different company, "
        "including subsidiaries/divisions that are usually reported separately, or if unsure.\n\n"
        + "\n".join(lines)
    )
    data = call_tool(prompt, MATCH_TOOL, max_tokens=4096, model=EXTRACTION_MODEL)
    out: dict[int, int | None] = {}
    for m in data.get("matches", []):
        idx, cid = m.get("index"), m.get("company_id")
        if isinstance(idx, int) and 0 <= idx < len(ambiguous):
            valid_ids = {c["id"] for c in ambiguous[idx][1]}
            out[idx] = cid if cid in valid_ids else None
    return out


def resolve_companies(names: list[str], industry_id: str | None, use_llm: bool = True) -> dict[str, int]:
    """Map each raw company name to a company id, creating companies as needed.

    Exact normalized matches are free; near-matches are confirmed by one batched
    Claude call. Names that normalize to nothing (e.g. 'Unknown') are omitted.
    """
    known = {c["normalized_name"]: c for c in store().list_companies()}
    resolved: dict[str, int] = {}
    pending: dict[str, str] = {}  # normalized -> display name for names needing a decision

    for raw in names:
        norm = normalize_name(raw)
        if not norm or raw in resolved:
            continue
        if norm in known:
            resolved[raw] = known[norm]["id"]
        else:
            pending.setdefault(norm, raw.strip())

    ambiguous: list[tuple[str, list[dict]]] = []
    new_names: list[str] = []
    for norm, display in pending.items():
        candidates = _fuzzy_candidates(norm, known)
        (ambiguous.append((display, candidates)) if candidates and use_llm else new_names.append(display))

    decisions: dict[int, int | None] = {}
    if ambiguous:
        try:
            decisions = _ask_claude_to_match(ambiguous)
        except Exception as e:
            logger.error(f"Company matching call failed; treating near-matches as new companies: {e}")

    by_norm: dict[str, int] = {}
    for i, (display, _) in enumerate(ambiguous):
        if decisions.get(i) is not None:
            by_norm[normalize_name(display)] = decisions[i]
        else:
            new_names.append(display)
    for display in new_names:
        by_norm[normalize_name(display)] = store().insert_company(display, normalize_name(display), industry_id)

    for raw in names:
        norm = normalize_name(raw)
        if raw not in resolved and norm in by_norm:
            resolved[raw] = by_norm[norm]
    if ambiguous or new_names:
        logger.info(
            f"Company resolution: {len(ambiguous)} near-matches checked, "
            f"{sum(1 for v in decisions.values() if v is not None)} merged, {len(new_names)} new companies"
        )
    return resolved


def ensure_companies(names: list[str], industry_id: str) -> None:
    """Create rows for names (e.g. a profile watchlist) using exact matching only."""
    for name in names:
        norm = normalize_name(name)
        if norm:
            cid = store().insert_company(name, norm, industry_id)
            store().touch_company(cid, industry_id, None)


def link_events(events: list[dict], use_llm: bool = True) -> int:
    """Set company_id on stored events (dicts with id, industry_id, company, date). Returns count linked."""
    linked = 0
    by_industry: dict[str, list[dict]] = {}
    for e in events:
        by_industry.setdefault(e["industry_id"], []).append(e)
    for industry_id, group in by_industry.items():
        mapping = resolve_companies([e["company"] for e in group if e.get("company")], industry_id, use_llm)
        for e in group:
            cid = mapping.get(e.get("company"))
            if cid is None:
                continue
            store().set_event_company(e["id"], cid)
            store().touch_company(cid, industry_id, e.get("date"))
            linked += 1
    return linked


def find_companies(name: str, limit: int = 8) -> list[dict]:
    """Best-first company matches for a user-supplied name (no LLM)."""
    norm = normalize_name(name)
    if not norm:
        return []
    known = {c["normalized_name"]: c for c in store().list_companies()}
    if norm in known:
        return [known[norm]]
    contains = [c for n, c in known.items() if norm in n or n in norm.split()]
    fuzzy = [known[n] for n in difflib.get_close_matches(norm, list(known), n=limit, cutoff=0.6)]
    out: list[dict] = []
    for c in contains + fuzzy:
        if c not in out:
            out.append(c)
    return out[:limit]
