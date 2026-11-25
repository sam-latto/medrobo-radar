"""One-time copy of the local SQLite data (medrobo.db) into Supabase Postgres.

Shared data (runs, briefs, events, companies, job boards and postings) is copied
as the app's starting data, and the three YAML profiles turn their industries on.
Personal Jobs data (resume, matches, tailoring advice, job preferences) is copied
onto one user's account with --owner-email, once that person has signed in.

The SQLite file is opened read-only. Re-running is safe: existing rows are skipped.

Usage:  python scripts/migrate_sqlite_to_postgres.py
        python scripts/migrate_sqlite_to_postgres.py --personal-only --owner-email you@umich.edu
"""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import DATABASE_URL, DB_PATH  # noqa: E402

PROFILES_DIR = ROOT / "profiles"
PREFS_KEY = "job_preferences"
# What the local dashboard used when no settings were saved (dashboard/jobs_tab.py DEFAULT_PREFS)
LOCAL_DEFAULT_PREFS = {"functions": ["Product Management", "Strategy"], "custom_functions": "",
                       "locations": "", "include_remote": True, "remote_only": False}


# ── Pure transforms (unit-tested) ────────────────────────────────────────────

def json_list(text: str | None) -> list:
    try:
        value = json.loads(text or "[]")
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def json_or_none(text: str | None):
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def as_bool(value) -> bool | None:
    return None if value is None else bool(value)


def utc(ts: str | None) -> str | None:
    """SQLite CURRENT_TIMESTAMP values are UTC without a zone."""
    return f"{ts}+00" if ts else None


def brief_headline(markdown: str) -> str | None:
    """The brief's headline: its first heading, or for older briefs whose first heading
    is just a title ("... Daily Briefing | <date>"), the first bold lead line."""
    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            if heading and not re.search(r"daily briefing", heading, re.I):
                return heading
            continue
        bold = re.fullmatch(r"\*\*(.+?)\*\*", line)
        if bold:
            return re.sub(r"^lead story:\s*", "", bold.group(1).strip(), flags=re.I) or None
    return None


def is_real_brief(text: str | None) -> bool:
    return bool(text) and not text.startswith("No ")


def pick_briefs(runs: list[dict]) -> list[dict]:
    """The latest complete run with a real brief, per industry per day."""
    best: dict[tuple[str, str], dict] = {}
    for run in runs:
        if run["status"] != "complete" or not is_real_brief(run["briefing_text"]):
            continue
        key = (run["industry_id"], run["run_date"])
        if key not in best or (run["completed_at"] or "") > (best[key]["completed_at"] or ""):
            best[key] = run
    return list(best.values())


def job_preferences_row(prefs: dict, industry_ids: list[str]) -> dict:
    locations = [p.strip() for p in (prefs.get("locations") or "").split(",") if p.strip()]
    return {
        "role_types": list(prefs.get("functions") or []),
        "custom_role_types": prefs.get("custom_functions") or "",
        "locations": locations,
        "include_remote": bool(prefs.get("include_remote", True)),
        "remote_only": bool(prefs.get("remote_only", False)),
        "industry_ids": industry_ids,
    }


# ── Copy steps ───────────────────────────────────────────────────────────────

def _rows(src: sqlite3.Connection, sql: str) -> list[dict]:
    return [dict(r) for r in src.execute(sql)]


def activate_profiles(pg) -> list[str]:
    activated = []
    for path in sorted(PROFILES_DIR.glob("*.yaml")):
        industry_id = path.stem
        updated = pg.execute(
            "update public.industries set profile_yaml = %s, status = 'active' "
            "where id = %s and kind = 'industry'",
            (path.read_text(encoding="utf-8"), industry_id)).rowcount
        if updated:
            activated.append(industry_id)
        else:
            print(f"  ! no catalog entry for profile {industry_id}; skipped")
    return activated


def copy_shared(src, pg) -> dict[str, int]:
    counts = {}

    runs = _rows(src, "select * from pipeline_runs")
    with pg.cursor() as cur:
        cur.executemany(
            "insert into public.pipeline_runs (id, industry_id, run_date, triggered_by, status, "
            "source_counts, completed_at) values (%s, %s, %s, %s, %s, %s, %s) on conflict (id) do nothing",
            [(r["id"], r["industry_id"], r["run_date"], r["triggered_by"], r["status"],
              Jsonb(json_or_none(r["source_counts"])) if r["source_counts"] else None,
              utc(r["completed_at"])) for r in runs])
    counts["pipeline_runs"] = len(runs)

    briefs = pick_briefs(runs)
    with pg.cursor() as cur:
        cur.executemany(
            "insert into public.briefs (industry_id, run_id, brief_date, headline, content_md, created_at) "
            "values (%s, %s, %s, %s, %s, coalesce(%s::timestamptz, now())) "
            "on conflict (industry_id, brief_date) do nothing",
            [(b["industry_id"], b["id"], b["run_date"], brief_headline(b["briefing_text"]),
              b["briefing_text"], utc(b["completed_at"])) for b in briefs])
    counts["briefs"] = len(briefs)

    companies = _rows(src, "select * from companies")
    with pg.cursor() as cur:
        cur.executemany(
            "insert into public.companies (id, name, normalized_name, website, first_seen, last_seen) "
            "overriding system value values (%s, %s, %s, %s, %s, %s) on conflict do nothing",
            [(c["id"], c["name"], c["normalized_name"], c["website"], c["first_seen"], c["last_seen"])
             for c in companies])
        cur.executemany(
            "insert into public.company_industries (company_id, industry_id) values (%s, %s) "
            "on conflict do nothing",
            [(c["id"], i) for c in companies for i in dict.fromkeys(json_list(c["industry_ids"]))])
    counts["companies"] = len(companies)

    events = _rows(src, "select * from events")
    with pg.cursor() as cur:
        cur.executemany(
            "insert into public.events (id, run_id, industry_id, company_id, company, product, event_type, "
            "segment, date, summary, source_url, source_type, funding_amount, regulatory_status, created_at) "
            "overriding system value values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "on conflict do nothing",
            [(e["id"], e["run_id"], e["industry_id"], e["company_id"], e["company"], e["product"],
              e["event_type"], e["segment"], e["date"], e["summary"], e["source_url"], e["source_type"],
              e["funding_amount"], e["regulatory_status"], utc(e["created_at"])) for e in events])
    counts["events"] = len(events)

    boards = _rows(src, "select * from job_boards")
    with pg.cursor() as cur:
        cur.executemany(
            "insert into public.job_boards (provider, token, company, last_fetched_at) "
            "values (%s, %s, %s, %s) on conflict do nothing",
            [(b["provider"], b["token"], b["company"], utc(b["last_fetched_at"])) for b in boards])
    counts["job_boards"] = len(boards)

    checks = _rows(src, "select * from job_board_checks")
    with pg.cursor() as cur:
        cur.executemany(
            "insert into public.job_board_checks (normalized_name, checked_at) values (%s, %s) "
            "on conflict do nothing",
            [(c["normalized_name"], utc(c["checked_at"])) for c in checks])
    counts["job_board_checks"] = len(checks)

    postings = _rows(src, "select * from job_postings")
    with pg.cursor() as cur:
        cur.executemany(
            "insert into public.job_postings (id, url, provider, board_token, company, title, location, "
            "remote, department, employment_type, compensation, description, posted_at, active, "
            "first_seen_at, last_seen_at) overriding system value "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict do nothing",
            [(p["id"], p["url"], p["provider"], p["board_token"], p["company"], p["title"], p["location"],
              as_bool(p["remote"]), p["department"], p["employment_type"], p["compensation"],
              p["description"], p["posted_at"], bool(p["active"]), utc(p["first_seen_at"]),
              utc(p["last_seen_at"])) for p in postings])
        cur.executemany(
            "insert into public.job_posting_industries (job_id, industry_id) values (%s, %s) "
            "on conflict do nothing",
            [(p["id"], i) for p in postings for i in dict.fromkeys(json_list(p["industry_ids"]))])
    counts["job_postings"] = len(postings)

    # Identity columns continue after the copied ids
    for table in ("companies", "events", "job_postings"):
        pg.execute(
            f"select setval(pg_get_serial_sequence('public.{table}', 'id'), "
            f"coalesce((select max(id) from public.{table}), 0) + 1, false)")
    return counts


def copy_personal(src, pg, owner_email: str, industry_ids: list[str]) -> dict[str, int] | None:
    owner = pg.execute("select id from public.profiles where email = %s",
                       (owner_email.lower(),)).fetchone()
    if owner is None:
        print(f"  ! no account for {owner_email} yet: sign in to the app once, then re-run with --owner-email")
        return None
    user_id = owner[0]

    setting = src.execute("select value from app_settings where key = ?", (PREFS_KEY,)).fetchone()
    prefs = {**LOCAL_DEFAULT_PREFS, **((json_or_none(setting[0]) if setting else None) or {})}
    pref_row = job_preferences_row(prefs or {}, industry_ids)

    resume_ids = {}
    for r in _rows(src, "select * from resumes order by id"):
        existing = pg.execute("select id from public.resumes where user_id = %s and filename is not distinct from %s "
                              "and text = %s", (user_id, r["filename"], r["text"])).fetchone()
        if existing:
            resume_ids[r["id"]] = existing[0]
            continue
        resume_ids[r["id"]] = pg.execute(
            "insert into public.resumes (user_id, filename, text, role_types, created_at) "
            "values (%s, %s, %s, %s, %s) returning id",
            (user_id, r["filename"], r["text"], pref_row["role_types"], utc(r["created_at"]))).fetchone()[0]

    matches = _rows(src, "select * from job_matches")
    with pg.cursor() as cur:
        cur.executemany(
            "insert into public.job_matches (user_id, job_id, resume_id, score, summary, strengths, gaps, "
            "level_fit, industry_match, created_at) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "on conflict do nothing",
            [(user_id, m["job_id"], resume_ids[m["resume_id"]], m["score"], m["summary"],
              Jsonb(json_list(m["strengths"])), Jsonb(json_list(m["gaps"])), m["level_fit"],
              as_bool(m["industry_match"]), utc(m["created_at"]))
             for m in matches if m["resume_id"] in resume_ids])

    advice = _rows(src, "select * from resume_advice")
    with pg.cursor() as cur:
        cur.executemany(
            "insert into public.resume_advice (user_id, resume_id, kind, key, content, created_at) "
            "values (%s, %s, %s, %s, %s, %s) on conflict do nothing",
            [(user_id, resume_ids[a["resume_id"]], a["kind"], a["key"], Jsonb(json_or_none(a["content"])),
              utc(a["created_at"])) for a in advice if a["resume_id"] in resume_ids])

    pg.execute(
        "insert into public.job_preferences (user_id, role_types, custom_role_types, locations, "
        "include_remote, remote_only, industry_ids) values (%(user_id)s, %(role_types)s, "
        "%(custom_role_types)s, %(locations)s, %(include_remote)s, %(remote_only)s, %(industry_ids)s) "
        "on conflict (user_id) do nothing", {"user_id": user_id, **pref_row})

    with pg.cursor() as cur:
        cur.executemany(
            "insert into public.subscriptions (user_id, industry_id) values (%s, %s) on conflict do nothing",
            [(user_id, i) for i in industry_ids])
    return {"resumes": len(resume_ids), "job_matches": len(matches), "resume_advice": len(advice)}


def verify(src, pg) -> bool:
    checks = {
        "pipeline_runs": ("select count(*) from pipeline_runs", "select count(*) from public.pipeline_runs"),
        "events": ("select count(*) from events", "select count(*) from public.events"),
        "companies": ("select count(*) from companies", "select count(*) from public.companies"),
        "job_postings": ("select count(*) from job_postings", "select count(*) from public.job_postings"),
        "job_boards": ("select count(*) from job_boards", "select count(*) from public.job_boards"),
        "job_board_checks": ("select count(*) from job_board_checks",
                             "select count(*) from public.job_board_checks"),
    }
    ok = True
    print("\n  table              sqlite  postgres")
    for name, (s_sql, p_sql) in checks.items():
        s = src.execute(s_sql).fetchone()[0]
        p = pg.execute(p_sql).fetchone()[0]
        ok &= p >= s
        print(f"  {name:<18} {s:>6}  {p:>8}  {'ok' if p >= s else 'MISSING ROWS'}")
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner-email", help="copy personal Jobs data onto this user's account")
    parser.add_argument("--sqlite", default=DB_PATH, help="path to the SQLite file (default: DB_PATH)")
    parser.add_argument("--personal-only", action="store_true",
                        help="copy only the owner's Jobs data (use after the first full copy)")
    args = parser.parse_args()
    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL is not set (see .env)")

    src = sqlite3.connect(f"file:{Path(args.sqlite).resolve().as_posix()}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    with psycopg.connect(DATABASE_URL) as pg:
        with pg.transaction():
            activated = activate_profiles(pg)
            print(f"  industries turned on: {', '.join(activated)}")
            if not args.personal_only:
                for table, n in copy_shared(src, pg).items():
                    print(f"  copied {n:>4} {table}")
            if args.owner_email:
                personal = copy_personal(src, pg, args.owner_email, activated)
                for table, n in (personal or {}).items():
                    print(f"  copied {n:>4} {table} for {args.owner_email}")
        ok = verify(src, pg)
    src.close()
    print("\n  every SQLite row is in Postgres" if ok else "\n  some rows are missing; see above")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
