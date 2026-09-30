"""
CLI entry point for Industry Radar.

Usage:
  python main.py run [profile_id] [--force]   # Run pipeline once (all active profiles by default)
  python main.py new-profile "battery storage" # Draft a new industry profile (saved inactive)
  python main.py profiles                      # List industry profiles
  python main.py scheduler                     # Start background scheduler only
  python main.py                               # Start scheduler + Streamlit dashboard
"""
import logging
import subprocess
import sys
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from config import PIPELINE_SCHEDULE_HOUR, PIPELINE_SCHEDULE_MINUTE
from database.db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _run_pipeline():
    from pipeline.pipeline import run_pipeline
    result = run_pipeline(triggered_by="schedule")
    logger.info(f"Scheduled pipeline result: {result}")


def start_background_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _run_pipeline,
        "cron",
        hour=PIPELINE_SCHEDULE_HOUR,
        minute=PIPELINE_SCHEDULE_MINUTE,
        id="daily_pipeline",
    )
    scheduler.start()
    logger.info(
        f"Scheduler started — pipeline runs daily at {PIPELINE_SCHEDULE_HOUR:02d}:{PIPELINE_SCHEDULE_MINUTE:02d}"
    )
    return scheduler


def cmd_run(args: list[str]):
    from pipeline.pipeline import run_pipeline
    init_db()
    force = "--force" in args
    positional = [a for a in args if not a.startswith("--")]
    profile_id = positional[0] if positional else None
    result = run_pipeline(profile_id=profile_id, triggered_by="manual", force=force)
    print(f"\nPipeline {result['status']}: {result['events']} events, {result['notable']} notable")
    for r in result["runs"]:
        line = f"  - {r['industry_id']}: {r['status']}, {r['events']} events, {r['notable']} notable"
        if r.get("error"):
            line += f" (error: {r['error']})"
        print(line)
        for name, c in (r.get("source_counts") or {}).items():
            print(f"      {name}: {c.get('fetched', 0)} fetched, {c.get('new', 0)} new"
                  + (f" (error: {c['error']})" if c.get("error") else ""))


def cmd_new_profile(args: list[str]):
    from pipeline.profile_agent import draft_and_save_profile
    if not args:
        print('Usage: python main.py new-profile "industry name"')
        sys.exit(1)
    profile, path = draft_and_save_profile(" ".join(args))
    print(f"Drafted profile '{profile.id}' -> {path}")
    print("It is saved with active: false. Review it, then set active: true (or activate it in the dashboard).")


def cmd_profiles():
    from profiles.loader import load_profiles
    for p in load_profiles():
        status = "active" if p.active else "inactive"
        print(f"{p.id:<28} {status:<9} {p.label} ({len(p.segments)} segments, sources: {', '.join(p.sources)})")


def cmd_scheduler():
    init_db()
    scheduler = BlockingScheduler()
    scheduler.add_job(
        _run_pipeline,
        "cron",
        hour=PIPELINE_SCHEDULE_HOUR,
        minute=PIPELINE_SCHEDULE_MINUTE,
        id="daily_pipeline",
    )
    logger.info(
        f"Scheduler running — pipeline will fire at {PIPELINE_SCHEDULE_HOUR:02d}:{PIPELINE_SCHEDULE_MINUTE:02d} daily"
    )
    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped")


def cmd_default():
    """Start scheduler in background thread, then launch Streamlit."""
    init_db()
    start_background_scheduler()
    logger.info("Starting Streamlit dashboard...")
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", "dashboard/app.py", "--server.port", "8501"],
        check=True,
    )


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "default"
    args = sys.argv[2:]

    if command == "run":
        cmd_run(args)
    elif command == "new-profile":
        cmd_new_profile(args)
    elif command == "profiles":
        cmd_profiles()
    elif command == "scheduler":
        cmd_scheduler()
    else:
        cmd_default()
