"""Daily worker: collect, extract and brief every followed industry, refresh job postings,
email opted-in users, and keep spend within the monthly budget.

Usage:  python -m worker.run_daily                      # the scheduled run
        python -m worker.run_daily --dry-run            # list what would run, change nothing
        python -m worker.run_daily --industry physical_ai --force --no-email --no-jobs
"""
import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from api import focus
from api.limits import SPEND_PAUSED_KEY, period_bounds
from api.usage import UsageCollector
from config import DATABASE_URL
from database.pg_store import PgStore
from database.store import use_store
from email_digest import sender
from jobs.collector import collect_jobs
from pipeline import pipeline
from pipeline.llm import batch_mode, usage_sink
from profiles.loader import parse_profile

logger = logging.getLogger("worker")

MONTHLY_BUDGET_USD = float(os.getenv("MONTHLY_BUDGET_USD", "300"))
BUDGET_WARN_AT = 0.8
# Industries with fewer followers than this get a weekly brief instead of a daily one.
LOW_FOLLOWER_THRESHOLD = int(os.getenv("LOW_FOLLOWER_THRESHOLD", "3"))
LOW_FOLLOWER_EVERY_DAYS = 7
# Overnight Claude calls go through the Message Batches API at half price.
USE_BATCH_API = os.getenv("WORKER_USE_BATCH_API", "1") == "1"


# ── What to run ──────────────────────────────────────────────────────────────

def industries_to_run(conn, only: list[str] | None = None) -> list[dict]:
    """Active industries with a pipeline profile and at least one follower (or the ones named)."""
    return conn.execute(
        """select i.id, i.name, i.profile_yaml,
                  (select count(*) from public.subscriptions s where s.industry_id = i.id) as followers,
                  (select max(r.run_date) from public.pipeline_runs r
                   where r.industry_id = i.id and r.status = 'complete') as last_run_date,
                  exists (select 1 from public.pipeline_runs r
                          where r.industry_id = i.id and r.status = 'complete') as has_run_before
           from public.industries i
           where i.kind = 'industry' and i.status = 'active' and i.profile_yaml is not null
             and (%(only)s::text[] is not null and i.id = any(%(only)s)
                  or %(only)s::text[] is null
                     and exists (select 1 from public.subscriptions s where s.industry_id = i.id))
           order by i.sort_order""", {"only": only}).fetchall()


def due_today(industry: dict, today: date) -> bool:
    """Daily for industries with enough followers; weekly otherwise (first run: right away)."""
    if industry["followers"] >= LOW_FOLLOWER_THRESHOLD or industry["last_run_date"] is None:
        return True
    return today - industry["last_run_date"] >= timedelta(days=LOW_FOLLOWER_EVERY_DAYS)


def role_types_for(conn, industry_id: str) -> list[str]:
    """Role types job seekers want in this industry (drives the career-site search)."""
    rows = conn.execute(
        """select jp.role_types, jp.custom_role_types from public.job_preferences jp
           where %(industry)s = any(jp.industry_ids)
              or (cardinality(jp.industry_ids) = 0 and exists (
                    select 1 from public.subscriptions s where s.user_id = jp.user_id
                    and s.industry_id = %(industry)s))""", {"industry": industry_id}).fetchall()
    types: list[str] = []
    for r in rows:
        types += r["role_types"] + [t.strip() for t in r["custom_role_types"].split(",") if t.strip()]
    return list(dict.fromkeys(types))


# ── Steps ────────────────────────────────────────────────────────────────────

def _with_usage(conn, industry_id: str, fn):
    """Run fn() with Claude/search costs recorded against the industry; returns (result, cost)."""
    collector = UsageCollector()
    token = usage_sink.set(collector.add)
    try:
        result = fn()
    finally:
        usage_sink.reset(token)
        cost = collector.total_cost
        collector.flush(conn, None, industry_id)
    return result, cost


def run_industry(conn, industry: dict, run_date: str, force: bool) -> dict:
    profile = parse_profile(industry["profile_yaml"])
    store = PgStore(conn)
    token = batch_mode.set(USE_BATCH_API)
    try:
        with use_store(store):
            result, cost = _with_usage(
                conn, industry["id"], lambda: pipeline._run_profile(profile, run_date, "schedule", force))
    finally:
        batch_mode.reset(token)
    if result.get("run_id"):
        store.set_run_cost(result["run_id"], cost)
    return {**result, "cost_usd": cost}


def refresh_jobs(conn, industry: dict) -> dict:
    profile = parse_profile(industry["profile_yaml"])
    functions = role_types_for(conn, industry["id"])
    with use_store(PgStore(conn)):
        stats, cost = _with_usage(conn, industry["id"], lambda: collect_jobs(profile, functions))
    return {**stats, "role_types": len(functions), "cost_usd": cost}


def email_users(conn, run_date: str) -> int:
    """Send each opted-in user today's briefs for the industries they follow. A user's focus
    (e.g. "Surgical robotics") adds a short list of matching events from the same run."""
    users = conn.execute(
        """select p.email, array_agg(s.industry_id order by i.sort_order) as industry_ids,
                  jsonb_object_agg(s.industry_id, s.focus) filter (where s.focus is not null) as focuses
           from public.profiles p
           join public.subscriptions s on s.user_id = p.id
           join public.industries i on i.id = s.industry_id
           where p.email_brief_opt_in group by p.id, p.email""").fetchall()
    briefs = {b["industry_id"]: b for b in conn.execute(
        """select b.industry_id, b.run_id, i.name as industry_name, b.content_md as briefing_text
           from public.briefs b join public.industries i on i.id = b.industry_id
           where b.brief_date = %s""", (run_date,))}
    run_events = {}
    for b in briefs.values():
        run_events[b["industry_id"]] = conn.execute(
            "select company, product, segment, summary, source_url from public.events where run_id = %s",
            (b["run_id"],)).fetchall() if b["run_id"] else []
    sent = 0
    for u in users:
        sections = []
        for industry_id in u["industry_ids"]:
            if industry_id not in briefs:
                continue
            section = dict(briefs[industry_id])
            label = (u["focuses"] or {}).get(industry_id)
            stems = focus.focus_stems(label, section["industry_name"])
            if stems:
                section["focus_label"] = label
                section["focus_events"] = [e for e in run_events[industry_id] if focus.matches(e, stems)][:8]
            sections.append(section)
        try:
            sent += sender.send_user_digest(u["email"], sections, run_date)
        except Exception as e:
            logger.error(f"Digest email failed for one user: {e}")
    return sent


def month_spend(conn, now: datetime) -> float:
    start, _ = period_bounds("month", now)
    row = conn.execute("select coalesce(sum(cost_usd), 0) as total from public.usage_events where created_at >= %s",
                       (start,)).fetchone()
    return float(row["total"])


def check_budget(conn, now: datetime | None = None, budget: float = MONTHLY_BUDGET_USD) -> dict:
    """Warn at 80% of the monthly budget; at 100% pause per-user Claude features until month end."""
    now = now or datetime.now(timezone.utc)
    store = PgStore(conn)
    spent = month_spend(conn, now)
    month = f"{now:%Y-%m}"
    _, month_end = period_bounds("month", now)
    status = {"spent_usd": round(spent, 2), "budget_usd": budget, "paused": False}
    if spent >= budget * BUDGET_WARN_AT and not store.get_cache(f"budget_alert:{month}:80"):
        sender.send_admin_alert(
            f"Industry Radar has used {spent / budget:.0%} of this month's budget",
            f"<p>${spent:,.2f} of ${budget:,.0f} spent in {now:%B %Y}.</p>")
        store.set_cache(f"budget_alert:{month}:80", True, expires_at=month_end)
    if spent >= budget:
        status["paused"] = True
        if not store.get_cache(SPEND_PAUSED_KEY):
            store.set_cache(SPEND_PAUSED_KEY, {"month": month, "spent_usd": round(spent, 2)}, expires_at=month_end)
            sender.send_admin_alert(
                "Industry Radar paused job search and tailoring: monthly budget reached",
                f"<p>${spent:,.2f} of ${budget:,.0f} spent in {now:%B %Y}. Job search and resume tailoring are "
                f"paused, and new industries won't start, until {month_end:%B} 1. Daily briefs keep running.</p>"
                "<p>To resume sooner, raise MONTHLY_BUDGET_USD and delete the spend_paused row from the cache "
                "table.</p>")
    return status


# ── Main ─────────────────────────────────────────────────────────────────────

def run(conn, only: list[str] | None = None, force: bool = False, send_email: bool = True,
        jobs: bool = True, run_date: str | None = None) -> dict:
    run_date = run_date or date.today().isoformat()
    budget = check_budget(conn)
    industries = industries_to_run(conn, only)
    if not only and not force:
        today = date.fromisoformat(run_date)
        weekly = [i["id"] for i in industries if not due_today(i, today)]
        if weekly:
            logger.info(f"Not due today (fewer than {LOW_FOLLOWER_THRESHOLD} followers, weekly): {weekly}")
        industries = [i for i in industries if due_today(i, today)]
    if budget["paused"]:
        skipped = [i["id"] for i in industries if not i["has_run_before"]]
        if skipped:
            logger.warning(f"Budget reached: not starting new industries {skipped}")
        industries = [i for i in industries if i["has_run_before"]]

    results = []
    for industry in industries:
        logger.info(f"[{industry['id']}] starting")
        result = run_industry(conn, industry, run_date, force)
        if result["status"] == "failed":
            logger.warning(f"[{industry['id']}] failed; retrying once")
            result = run_industry(conn, industry, run_date, force=True)
        if jobs and result["status"] != "failed":
            try:
                result["jobs"] = refresh_jobs(conn, industry)
            except Exception as e:
                logger.exception(f"[{industry['id']}] job refresh failed")
                result["jobs"] = {"error": str(e)}
        results.append(result)

    failures = [(r["industry_id"], r.get("error")) for r in results if r["status"] == "failed"]
    if failures:
        sender.send_failure_alert(failures)
    emailed = email_users(conn, run_date) if send_email else 0
    summary = {
        "run_date": run_date,
        "industries": [{"id": r["industry_id"], "status": r["status"], "events": r["events"],
                        "cost_usd": r["cost_usd"], "jobs": r.get("jobs")} for r in results],
        "failed": [f[0] for f in failures],
        "emails_sent": emailed,
        "cost_usd": round(sum(r["cost_usd"] + (r.get("jobs") or {}).get("cost_usd", 0) for r in results), 4),
        "budget": check_budget(conn),
    }
    logger.info(f"Daily run summary: {summary}")
    return summary


def connect():
    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL is not set")
    return psycopg.connect(DATABASE_URL, autocommit=True, row_factory=dict_row, prepare_threshold=None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--industry", action="append", help="run only this industry (repeatable)")
    parser.add_argument("--force", action="store_true", help="run even if it already completed today")
    parser.add_argument("--no-email", action="store_true", help="don't email users")
    parser.add_argument("--no-jobs", action="store_true", help="don't refresh job postings")
    parser.add_argument("--dry-run", action="store_true", help="list what would run and the budget; change nothing")
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    with connect() as conn:
        if args.dry_run:
            industries = industries_to_run(conn, args.industry)
            if not args.industry and not args.force:
                industries = [i for i in industries if due_today(i, date.today())]
            spent = month_spend(conn, datetime.now(timezone.utc))
            print(f"Would run: {', '.join(i['id'] for i in industries) or 'nothing (no followed industries due today)'}")
            print(f"Claude calls via Message Batches (half price): {'yes' if USE_BATCH_API else 'no'}")
            print(f"Spent this month: ${spent:,.2f} of ${MONTHLY_BUDGET_USD:,.0f}")
            return 0
        summary = run(conn, args.industry, args.force, not args.no_email, not args.no_jobs)
    return 1 if summary["failed"] and len(summary["failed"]) == len(summary["industries"]) else 0


if __name__ == "__main__":
    sys.exit(main())
