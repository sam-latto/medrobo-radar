import logging
from datetime import date
from tavily import TavilyClient
from config import TAVILY_API_KEY
from database.db import url_exists
from pipeline.models import RawResult

logger = logging.getLogger(__name__)

EVENT_TYPES = ["funding", "fda", "launch", "research", "news"]
SUB_SEGMENTS = ["surgical", "rehabilitation", "diagnostics", "exoskeletons", "AI-assisted"]

QUERY_TEMPLATES = {
    "funding": "{sub_segment} robotics company funding round investment 2025",
    "fda": "{sub_segment} medical robot FDA clearance approval De Novo 2025",
    "launch": "{sub_segment} robotics new product launch announcement 2025",
    "research": "{sub_segment} robotics clinical trial study results 2025",
    "news": "{sub_segment} healthcare robotics news development 2025",
}


def run_search_agent() -> list[RawResult]:
    client = TavilyClient(api_key=TAVILY_API_KEY)
    results: list[RawResult] = []
    seen_urls: set[str] = set()

    for event_type in EVENT_TYPES:
        for sub_segment in SUB_SEGMENTS:
            query = QUERY_TEMPLATES[event_type].format(sub_segment=sub_segment)
            query_tag = f"{event_type}:{sub_segment}"
            logger.info(f"Searching: {query}")

            try:
                response = client.search(
                    query=query,
                    search_depth="advanced",
                    max_results=5,
                    include_raw_content=False,
                )
                for item in response.get("results", []):
                    url = item.get("url", "")
                    if not url or url in seen_urls or url_exists(url):
                        continue
                    seen_urls.add(url)
                    results.append(
                        RawResult(
                            title=item.get("title", ""),
                            url=url,
                            snippet=item.get("content", ""),
                            source=item.get("url", "").split("/")[2] if "/" in url else url,
                            date=item.get("published_date"),
                            query_tag=query_tag,
                        )
                    )
            except Exception as e:
                logger.error(f"Search failed for query '{query}': {e}")

    logger.info(f"Search agent found {len(results)} new results")
    return results
