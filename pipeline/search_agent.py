import logging
from tavily import TavilyClient
from config import TAVILY_API_KEY, SEARCH_TIME_RANGE
from database.db import url_exists
from pipeline.models import RawResult
from profiles.loader import IndustryProfile

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


def run_search_agent(profile: IndustryProfile) -> list[RawResult]:
    settings = profile.sources.get("tavily", {})
    client = TavilyClient(api_key=TAVILY_API_KEY)
    results: list[RawResult] = []
    seen_urls: set[str] = set()

    for query, query_tag in build_queries(profile):
        logger.info(f"Searching: {query}")
        try:
            response = client.search(
                query=query,
                search_depth=settings.get("search_depth", "advanced"),
                max_results=settings.get("max_results", 5),
                include_raw_content=False,
                time_range=settings.get("time_range", SEARCH_TIME_RANGE),
            )
            for item in response.get("results", []):
                url = item.get("url", "")
                if not url or url in seen_urls or url_exists(url, profile.id):
                    continue
                seen_urls.add(url)
                results.append(
                    RawResult(
                        title=item.get("title", ""),
                        url=url,
                        snippet=item.get("content", ""),
                        source=url.split("/")[2] if url.count("/") >= 2 else url,
                        date=item.get("published_date"),
                        query_tag=query_tag,
                        source_type="tavily",
                    )
                )
        except Exception as e:
            logger.error(f"Search failed for query '{query}': {e}")

    logger.info(f"[{profile.id}] Search agent found {len(results)} new results")
    return results
