"""Job functions (e.g. Product Management) and the title keywords that identify them."""
import re

from database import jobs_db
from pipeline.llm import call_tool

FUNCTION_KEYWORDS: dict[str, list[str]] = {
    "Product Management": [
        "product manager", "product management", "product owner", "product lead", "head of product",
        "director of product", "product director", "vp of product", "vp, product", "vp product",
        "chief product", "group product", "technical product",
    ],
    "Strategy": [
        "strategy", "strategic", "corporate development", "corp dev", "business operations", "bizops",
        "chief of staff", "strategic initiatives", "business strategy",
    ],
    "Business Development & Partnerships": [
        "business development", "partnership", "alliances", "bd manager", "bd director", "commercial lead",
    ],
    "Corporate Development & M&A": ["corporate development", "m&a", "mergers", "acquisitions", "corp dev"],
    "Program & Project Management": [
        "program manager", "program management", "technical program", "project manager", "pmo",
    ],
    "Operations": ["operations manager", "operations lead", "head of operations", "director of operations",
                   "strategy & operations", "strategy and operations", "supply chain", "manufacturing operations"],
    "Marketing": ["marketing"],
    "Sales & Account Management": ["sales", "account executive", "account manager", "customer success"],
    "Engineering": ["engineer", "engineering", "developer", "architect"],
    "Data & AI": ["data scientist", "machine learning", "ml engineer", "ai engineer", "research scientist",
                  "data engineer", "analytics"],
    "Clinical & Medical Affairs": ["clinical", "medical affairs", "medical science", "field clinical"],
    "Finance": ["finance", "fp&a", "financial analyst", "controller", "investor relations"],
}

_KEYWORD_TOOL = {
    "name": "title_keywords",
    "description": "Job-title keywords that identify a job function.",
    "input_schema": {
        "type": "object",
        "properties": {
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "5-12 lowercase phrases that appear in job titles for this function",
            }
        },
        "required": ["keywords"],
    },
}


# Where expanded keyword lists are cached; the web API points this at Postgres.
_cache = {"get": lambda key: jobs_db.get_setting(key), "set": lambda key, value: jobs_db.set_setting(key, value)}


def set_keyword_cache(get, set_) -> None:
    _cache["get"], _cache["set"] = get, set_


def keywords_for(function: str) -> list[str]:
    """Title keywords for a function; custom functions are expanded once by Claude and cached."""
    if function in FUNCTION_KEYWORDS:
        return FUNCTION_KEYWORDS[function]
    cache_key = f"function_keywords:{function.strip().lower()}"
    cached = _cache["get"](cache_key)
    if cached:
        return cached
    base = [function.strip().lower()]
    try:
        data = call_tool(
            f'List job-title keywords that identify roles in the "{function}" function '
            "(e.g. for 'Product Management': product manager, product owner, head of product).",
            _KEYWORD_TOOL, max_tokens=4096,
        )
        keywords = sorted({k.strip().lower() for k in data.get("keywords", []) if k.strip()} | set(base))
    except Exception:
        return base  # don't cache a fallback
    _cache["set"](cache_key, keywords)
    return keywords


def _keyword_re(keyword: str) -> re.Pattern:
    # Word-boundary match so "pm" doesn't hit "equipment"; "&" etc. are matched literally
    return re.compile(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", re.IGNORECASE)


def keyword_matchers(functions: list[str]) -> dict[str, list[re.Pattern]]:
    """Title patterns per function. Build once per search: looking keywords up is a cache
    read (and, the first time for a custom function, a Claude call)."""
    return {f: [_keyword_re(k) for k in keywords_for(f)] for f in dict.fromkeys(functions)}


def matching_functions(title: str, functions: list[str],
                       matchers: dict[str, list[re.Pattern]] | None = None) -> list[str]:
    """Which of `functions` a job title belongs to."""
    matchers = matchers or keyword_matchers(functions)
    return [f for f in functions if any(p.search(title or "") for p in matchers[f])]
