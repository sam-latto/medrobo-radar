"""Collection stage: run every source plug-in listed in the profile."""
import logging
from datetime import date, timedelta

from database.db import url_exists
from pipeline.models import RawResult
from profiles.loader import IndustryProfile
from sources.registry import get_source
from sources.trusted_domains import is_trusted
from sources.tavily_source import build_queries  # noqa: F401  (re-exported for callers/tests)

logger = logging.getLogger(__name__)


def run_search_agent(profile: IndustryProfile) -> tuple[list[RawResult], dict[str, dict]]:
    """Returns (new results across all sources, per-source counts).

    Results already stored for this industry, seen from an earlier source in
    this run, or linking to a site we don't trust, are dropped. Sources run in profile order; a failing source is
    logged and skipped.
    """
    results: list[RawResult] = []
    seen_urls: set[str] = set()
    counts: dict[str, dict] = {}

    for name, cfg in profile.sources.items():
        source = get_source(name)
        if source is None:
            logger.warning(f"[{profile.id}] Unknown source '{name}' in profile; skipping")
            counts[name] = {"fetched": 0, "new": 0, "error": "unknown source"}
            continue
        since = date.today() - timedelta(days=int(cfg.get("lookback_days", source.default_lookback_days)))
        try:
            fetched = source.fetch(profile, since)
        except Exception as e:
            logger.error(f"[{profile.id}] Source {name} failed: {e}")
            counts[name] = {"fetched": 0, "new": 0, "error": str(e)}
            continue

        new = []
        untrusted = 0
        for r in fetched:
            if not is_trusted(r.url, r.source_type, profile.trusted_domains):
                untrusted += 1
                continue
            if r.url in seen_urls or url_exists(r.url, profile.id):
                continue
            seen_urls.add(r.url)
            new.append(r)
        results.extend(new)
        counts[name] = {"fetched": len(fetched), "new": len(new)}
        if untrusted:
            counts[name]["untrusted"] = untrusted
        logger.info(f"[{profile.id}] Source {name}: {len(fetched)} fetched, {len(new)} new, "
                    f"{untrusted} from untrusted sites dropped")

    logger.info(f"[{profile.id}] Search agent found {len(results)} new results")
    return results, counts
