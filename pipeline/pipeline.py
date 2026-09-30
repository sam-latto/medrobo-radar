import uuid
import logging
from datetime import date
from database.db import init_db, insert_run, update_run, insert_event, get_all_runs
from pipeline.search_agent import run_search_agent
from pipeline.extraction_agent import run_extraction_agent
from pipeline.synthesis_agent import run_synthesis_agent
from email_digest.sender import send_digests
from profiles.loader import IndustryProfile, load_profile, load_profiles

logger = logging.getLogger(__name__)


def _already_ran_today(industry_id: str) -> bool:
    today = date.today().isoformat()
    return any(
        r["run_date"] == today and r["status"] == "complete" for r in get_all_runs(industry_id)
    )


def _run_profile(profile: IndustryProfile, run_date: str, triggered_by: str, force: bool) -> dict:
    """Run search -> extraction -> synthesis for one industry. Returns a summary dict."""
    base = {"industry_id": profile.id, "industry_name": profile.label}

    if not force and _already_ran_today(profile.id):
        logger.info(f"[{profile.id}] Pipeline already completed today — skipping")
        return {**base, "run_id": None, "events": 0, "notable": 0, "status": "skipped"}

    run_id = str(uuid.uuid4())
    insert_run(run_id, run_date, triggered_by, profile.id)
    logger.info(f"[{profile.id}] Pipeline run {run_id} started (triggered_by={triggered_by})")

    try:
        # Agent 1 — Search
        raw_results, source_counts = run_search_agent(profile)
        base["source_counts"] = source_counts

        if not raw_results:
            logger.warning(f"[{profile.id}] No new results found; completing run with empty briefing.")
            update_run(run_id, "complete", "No new events found in this run.", source_counts)
            return {**base, "run_id": run_id, "events": 0, "notable": 0, "status": "complete"}

        # Agent 2 — Extraction
        structured_events = run_extraction_agent(raw_results, profile)

        if not structured_events:
            update_run(run_id, "complete", "No events could be extracted from search results.", source_counts)
            return {**base, "run_id": run_id, "events": 0, "notable": 0, "status": "complete"}

        # Agent 3 — Synthesis
        briefing_text, notable_indices = run_synthesis_agent(structured_events, profile)

        # Persist events
        for i, event in enumerate(structured_events):
            event.is_notable = i in notable_indices
            insert_event(run_id, event.model_dump())

        update_run(run_id, "complete", briefing_text, source_counts)

        logger.info(
            f"[{profile.id}] Pipeline run {run_id} complete: "
            f"{len(structured_events)} events, {len(notable_indices)} notable"
        )
        return {
            **base,
            "run_id": run_id,
            "events": len(structured_events),
            "notable": len(notable_indices),
            "status": "complete",
            "briefing_text": briefing_text,
            "notable_events": [structured_events[i].model_dump() for i in notable_indices],
        }

    except Exception as e:
        logger.error(f"[{profile.id}] Pipeline run {run_id} failed: {e}", exc_info=True)
        update_run(run_id, "failed", source_counts=base.get("source_counts"))
        return {**base, "run_id": run_id, "events": 0, "notable": 0, "status": "failed", "error": str(e)}


def _overall_status(statuses: list[str]) -> str:
    if not statuses or all(s == "skipped" for s in statuses):
        return "skipped"
    if all(s == "failed" for s in statuses):
        return "failed"
    if "failed" in statuses:
        return "partial"
    return "complete"


def run_pipeline(profile_id: str | None = None, triggered_by: str = "schedule", force: bool = False) -> dict:
    """Run the research pipeline for one industry, or for every active profile if
    `profile_id` is None. Returns totals plus a per-industry summary in `runs`."""
    init_db()
    run_date = date.today().isoformat()

    profiles = [load_profile(profile_id)] if profile_id else load_profiles(active_only=True)
    if not profiles:
        logger.warning("No active industry profiles — nothing to run")

    runs = [_run_profile(p, run_date, triggered_by, force) for p in profiles]

    digest_sections = [r for r in runs if r["status"] == "complete" and r.get("briefing_text")]
    if digest_sections:
        try:
            send_digests(digest_sections, run_date)
        except Exception as e:
            logger.error(f"Email digest failed: {e}")

    errors = [f"{r['industry_id']}: {r['error']}" for r in runs if r.get("error")]
    summary = {
        "status": _overall_status([r["status"] for r in runs]),
        "events": sum(r["events"] for r in runs),
        "notable": sum(r["notable"] for r in runs),
        "runs": [{k: v for k, v in r.items() if k != "notable_events"} for r in runs],
    }
    if errors:
        summary["error"] = "; ".join(errors)
    return summary
