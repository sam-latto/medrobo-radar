import logging
from datetime import date

from tavily import TavilyClient

from config import TAVILY_API_KEY, SEARCH_TIME_RANGE
from pipeline.models import RawResult
from profiles.loader import IndustryProfile
from sources.trusted_domains import search_domains

logger = logging.getLogger(__name__)


def build_queries(profile: IndustryProfile) -> list[tuple[str, str]]:
    """Return (query, query_tag) pairs: profile.search_queries x profile.segments.

    A template without a {segment} placeholder runs once, tagged with segment 'other'.
    """
    queries: list[tuple[str, str]] = []
    for event_type, template in profile.search_queries.items():
        if "{segment}" not in template:
            queries.append((template, f"{event_type}:other"))
            continue
        for segment in profile.segments:
            query = template.format(segment=segment.replace("_", " "))
            queries.append((query, f"{event_type}:{segment}"))
    return queries


class TavilySource:
    name = "tavily"
    source_type = "tavily"
    description = "Web and news search across the profile's search_queries x segments. Always include."
    config_schema = {
        "type": "object",
        "properties": {
            "time_range": {"type": "string", "enum": ["day", "week", "month", "year"]},
            "max_results": {"type": "integer", "description": "Results per query (default 5)"},
        },
    }
    default_lookback_days = 7

    def fetch(self, profile: IndustryProfile, since: date) -> list[RawResult]:
        # Recency comes from Tavily's time_range filter rather than `since`
        settings = profile.sources.get(self.name, {})
        client = TavilyClient(api_key=TAVILY_API_KEY)
        trusted = search_domains(profile.trusted_domains)  # only search sites we trust
        results: list[RawResult] = []

        for query, query_tag in build_queries(profile):
            logger.info(f"Searching: {query}")
            try:
                response = client.search(
                    query=query,
                    search_depth=settings.get("search_depth", "advanced"),
                    max_results=settings.get("max_results", 5),
                    include_raw_content=False,
                    time_range=settings.get("time_range", SEARCH_TIME_RANGE),
                    include_domains=trusted,
                )
            except Exception as e:
                logger.error(f"Search failed for query '{query}': {e}")
                continue
            for item in response.get("results", []):
                url = item.get("url", "")
                if not url:
                    continue
                results.append(
                    RawResult(
                        title=item.get("title", ""),
                        url=url,
                        snippet=item.get("content", ""),
                        source=url.split("/")[2] if url.count("/") >= 2 else url,
                        date=item.get("published_date"),
                        query_tag=query_tag,
                        source_type=self.source_type,
                    )
                )
        return results
