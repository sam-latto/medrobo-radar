"""Draft a new industry profile from just an industry name."""
import logging
import re

from pipeline.llm import call_tool
from profiles.loader import IndustryProfile, normalize_key, profile_exists, save_profile
from sources.registry import SOURCES

logger = logging.getLogger(__name__)

def _sources_schema() -> dict:
    """Every registered source plug-in, with the config block it takes."""
    return {
        "type": "object",
        "description": (
            "Data sources to run for this industry, keyed by source name. Always include tavily "
            "(empty config is fine); add primary sources only where they fit the industry."
        ),
        "properties": {
            name: {**source.config_schema, "description": source.description}
            for name, source in SOURCES.items()
        },
        "required": ["tavily"],
    }


def build_profile_tool() -> dict:
    return {
        "name": "save_industry_profile",
        "description": "Save a complete industry-tracking profile.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Industry name in title case"},
                "description": {"type": "string", "description": "One-sentence scope of the industry"},
                "segments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "4-6 sub-segments in snake_case, e.g. grid_scale_storage",
                },
                "event_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "5-7 event types in snake_case. Must include funding and news; use "
                        "regulatory, launch, research, partnership_ma, deployment where they fit"
                    ),
                },
                "search_queries": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": (
                        "One web search query per event type, keyed by event type. Each must contain "
                        "the literal placeholder {segment}. Never include years or dates."
                    ),
                },
                "notable_rules": {
                    "type": "object",
                    "properties": {
                        "funding_threshold_usd": {
                            "type": "integer",
                            "description": "Funding amount (USD) that makes a round notable for this industry",
                        },
                        "always_notable_event_types": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Event types that are always notable (subset of event_types)",
                        },
                    },
                    "required": ["funding_threshold_usd", "always_notable_event_types"],
                },
                "sources": _sources_schema(),
                "watchlist": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "5-15 of the most important companies to watch in this industry",
                },
                "briefing_audience": {
                    "type": "string",
                    "description": "Who the daily briefing is written for, e.g. 'climate-tech investor'",
                },
            },
            "required": [
                "name", "description", "segments", "event_types", "search_queries",
                "notable_rules", "sources", "watchlist", "briefing_audience",
            ],
        },
    }


def _slug(name: str) -> str:
    return normalize_key(name) or "industry"


def _unique_id(base: str) -> str:
    candidate, n = base, 2
    while profile_exists(candidate):
        candidate, n = f"{base}_{n}", n + 1
    return candidate


def draft_profile(industry_name: str) -> IndustryProfile:
    """Ask Claude to draft a full profile for `industry_name`. Not saved; inactive."""
    prompt = f"""You are configuring an autonomous market-intelligence agent to track the "{industry_name}" industry for investors.

Design a tracking profile: the industry's main segments, the event types worth tracking, one web search query per event type (with a {{segment}} placeholder that will be replaced by each segment name), notable-event rules, the data sources to use, and a watchlist of the most important companies.

Search queries should find recent news for that event type; do not put years or dates in them. Call save_industry_profile with the profile."""

    data = call_tool(prompt, build_profile_tool(), max_tokens=8000)
    data["id"] = _unique_id(_slug(industry_name))
    data["active"] = False
    # Keep queries only for declared event types, and strip any years the model slipped in
    event_types = {normalize_key(t) for t in data.get("event_types", [])} | {"news"}
    data["search_queries"] = {
        k: re.sub(r"\s{2,}", " ", re.sub(r"\b(19|20)\d{2}\b", "", v)).strip()
        for k, v in (data.get("search_queries") or {}).items()
        if normalize_key(k) in event_types
    }
    profile = IndustryProfile.model_validate(data)
    logger.info(f"Drafted profile '{profile.id}' with {len(profile.segments)} segments")
    return profile


def draft_and_save_profile(industry_name: str) -> tuple[IndustryProfile, str]:
    profile = draft_profile(industry_name)
    path = save_profile(profile)
    return profile, str(path)
