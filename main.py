"""
CLI entry point for MedRobo Radar.

Usage:
  python main.py run           # Run pipeline once manually
  python main.py scheduler     # Start background scheduler only
  python main.py               # Start scheduler + Streamlit dashboard
"""
import logging
import subprocess
import sys
import threading
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


def cmd_run():
    from pipeline.pipeline import run_pipeline
    init_db()
    result = run_pipeline(triggered_by="manual")
    print(f"\nPipeline complete: {result}")


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

    if command == "run":
        cmd_run()
    elif command == "scheduler":
        cmd_scheduler()
    else:
        cmd_default()
