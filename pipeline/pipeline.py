import uuid
import logging
from datetime import date
from database.db import init_db, insert_run, update_run, insert_event, get_all_runs
from pipeline.search_agent import run_search_agent
from pipeline.extraction_agent import run_extraction_agent
from pipeline.synthesis_agent import run_synthesis_agent
from email_digest.sender import send_digest

logger = logging.getLogger(__name__)


def _already_ran_today() -> bool:
    runs = get_all_runs()
    today = date.today().isoformat()
    return any(r["run_date"] == today and r["status"] == "complete" for r in runs)


def run_pipeline(triggered_by: str = "schedule", force: bool = False) -> dict:
    """Run the full research pipeline. Returns a summary dict."""
    init_db()

    run_date = date.today().isoformat()

    if not force and _already_ran_today():
        logger.info("Pipeline already completed today — skipping")
        return {"run_id": None, "events": 0, "notable": 0, "status": "skipped"}

    run_id = str(uuid.uuid4())
    insert_run(run_id, run_date, triggered_by)
    logger.info(f"Pipeline run {run_id} started (triggered_by={triggered_by})")

    try:
        # Agent 1 — Search
        raw_results = run_search_agent()

        if not raw_results:
            logger.warning("No new results found; completing run with empty briefing.")
            update_run(run_id, "complete", "No new events found in this run.")
            return {"run_id": run_id, "events": 0, "notable": 0, "status": "complete"}

        # Agent 2 — Extraction
        structured_events = run_extraction_agent(raw_results)

        if not structured_events:
            update_run(run_id, "complete", "No events could be extracted from search results.")
            return {"run_id": run_id, "events": 0, "notable": 0, "status": "complete"}

        # Agent 3 — Synthesis
        briefing_text, notable_indices = run_synthesis_agent(structured_events)

        # Persist events
        for i, event in enumerate(structured_events):
            event.is_notable = i in notable_indices
            insert_event(run_id, event.model_dump())

        update_run(run_id, "complete", briefing_text)

        notable_events = [
            structured_events[i].model_dump() for i in notable_indices if i < len(structured_events)
        ]

        # Send email digest
        try:
            send_digest(briefing_text, notable_events, run_date)
        except Exception as e:
            logger.error(f"Email digest failed: {e}")

        summary = {
            "run_id": run_id,
            "events": len(structured_events),
            "notable": len(notable_indices),
            "status": "complete",
            "briefing_text": briefing_text,
        }
        logger.info(f"Pipeline run {run_id} complete: {len(structured_events)} events, {len(notable_indices)} notable")
        return summary

    except Exception as e:
        logger.error(f"Pipeline run {run_id} failed: {e}", exc_info=True)
        update_run(run_id, "failed")
        return {"run_id": run_id, "events": 0, "notable": 0, "status": "failed", "error": str(e)}
