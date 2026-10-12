"""Daily worker and Postgres store, against the Supabase dev database.

Opt-in: RUN_DB_TESTS=1 python -m pytest tests/test_worker.py
Each test runs inside one transaction that is rolled back. Search, extraction, synthesis,
job boards and email are faked, so nothing is spent or sent.
"""
import json
import os
import uuid
from datetime import date, datetime, timezone

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg.rows import dict_row  # noqa: E402

from api import limits  # noqa: E402
from api.errors import ApiError  # noqa: E402
from config import DATABASE_URL  # noqa: E402
from database.pg_store import PgStore  # noqa: E402
from database.store import store, use_store  # noqa: E402
from email_digest import sender  # noqa: E402
from pipeline import companies as pipeline_companies  # noqa: E402
from pipeline import pipeline  # noqa: E402
from pipeline.llm import usage_sink  # noqa: E402
from pipeline.models import ExtractedEvent, RawResult  # noqa: E402
from worker import run_daily  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1" or not DATABASE_URL,
    reason="live database tests; set RUN_DB_TESTS=1 and DATABASE_URL")

TODAY = date.today().isoformat()


@pytest.fixture
def conn():
    c = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    c.execute("select 1")  # open the outer transaction now, so every write is rolled back at the end
    try:
        yield c
    finally:
        c.rollback()
        c.close()


@pytest.fixture
def mail(monkeypatch):
    sent = []
    monkeypatch.setattr(sender, "EMAIL_FROM", "radar@example.com")
    monkeypatch.setattr(sender, "EMAIL_TO", "owner@example.com")
    monkeypatch.setattr(sender, "_send", lambda subject, body, to=None: sent.append(
        {"subject": subject, "body": body, "to": to or "owner@example.com"}))
    return sent


def new_user(conn, industries=(), opt_in=False):
    user_id = uuid.uuid4()
    email = f"worker-test-{user_id.hex[:8]}@umich.edu"
    conn.execute("insert into auth.users (id, instance_id, aud, role, email, raw_user_meta_data) "
                 "values (%s, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', %s, %s)",
                 (user_id, email, json.dumps({})))
    conn.execute("update public.profiles set email_brief_opt_in = %s where id = %s", (opt_in, user_id))
    for i in industries:
        conn.execute("insert into public.subscriptions (user_id, industry_id) values (%s, %s)", (user_id, i))
    return user_id, email


def fake_pipeline(monkeypatch, fail=False):
    """Replace search/extraction/synthesis with fakes; synthesis 'spends' $0.25."""
    url = f"https://www.therobotreport.com/test-{uuid.uuid4().hex}"
    monkeypatch.setattr(pipeline, "run_search_agent", lambda profile: (
        [RawResult(title="t", url=url, snippet="s", source="therobotreport.com", query_tag="funding:other")],
        {"tavily": {"results": 1}}))
    monkeypatch.setattr(pipeline, "run_extraction_agent", lambda raw, profile: [ExtractedEvent(
        industry_id=profile.id, company="Brand New Robotics Co", event_type="funding", segment="humanoids",
        date="2026-10", summary="Raised $10M seed", source_url=url, funding_amount=10_000_000)])

    def synthesis(events, profile, previous, context):
        if fail:
            raise RuntimeError("synthesis exploded")
        usage_sink.get()({"purpose": "submit_briefing", "model": "claude-sonnet-4-6", "input_tokens": 1000,
                          "output_tokens": 500, "cache_creation_tokens": 0, "cache_read_tokens": 0,
                          "cost_usd": 0.25})
        return "# Humanoid makers raise again\n\nBody of the brief.", []
    monkeypatch.setattr(pipeline, "run_synthesis_agent", synthesis)
    monkeypatch.setattr(pipeline_companies, "call_tool", lambda *a, **k: {"matches": []})
    return url


def test_only_followed_industries_run(conn):
    assert run_daily.industries_to_run(conn) == []  # nobody follows anything yet
    new_user(conn, ["physical_ai", "consulting"])   # consulting has no pipeline profile yet
    assert [i["id"] for i in run_daily.industries_to_run(conn)] == ["physical_ai"]
    assert [i["id"] for i in run_daily.industries_to_run(conn, ["healthcare_robotics"])] == ["healthcare_robotics"]


def test_daily_run_writes_events_brief_cost_and_emails(conn, monkeypatch, mail):
    url = fake_pipeline(monkeypatch)
    _, email = new_user(conn, ["physical_ai"], opt_in=True)
    new_user(conn, ["physical_ai"], opt_in=False)

    summary = run_daily.run(conn, jobs=False)

    (result,) = summary["industries"]
    assert result["id"] == "physical_ai" and result["status"] == "complete" and result["events"] == 1
    assert result["cost_usd"] == 0.25 and summary["failed"] == []
    event = conn.execute("select * from public.events where source_url = %s", (url,)).fetchone()
    assert event["date"] == date(2026, 10, 1) and event["company_id"] is not None
    assert conn.execute("select 1 from public.company_industries where company_id = %s and industry_id = "
                        "'physical_ai'", (event["company_id"],)).fetchone()
    brief = conn.execute("select * from public.briefs where industry_id = 'physical_ai' and brief_date = %s",
                         (TODAY,)).fetchone()
    assert brief["headline"] == "Humanoid makers raise again" and brief["run_id"] == event["run_id"]
    run = conn.execute("select status, cost_usd from public.pipeline_runs where id = %s",
                       (event["run_id"],)).fetchone()
    assert run["status"] == "complete" and float(run["cost_usd"]) == 0.25
    usage = conn.execute("select action, industry_id, user_id from public.usage_events where industry_id = "
                         "'physical_ai'").fetchall()
    assert usage == [{"action": "llm:submit_briefing", "industry_id": "physical_ai", "user_id": None}]
    assert summary["emails_sent"] == 1
    assert [m["to"] for m in mail] == [email] and "Humanoid makers raise again" in mail[0]["body"]

    # Same day again: few followers, so it isn't due for a week (no duplicate brief or cost)
    again = run_daily.run(conn, jobs=False, send_email=False)
    assert again["industries"] == []
    # Even when forced, a run that already completed today is skipped by the pipeline
    forced = run_daily.run(conn, only=["physical_ai"], jobs=False, send_email=False)
    assert forced["industries"][0]["status"] == "skipped"


def test_weekly_industries_wait_and_popular_ones_run_daily(conn, monkeypatch):
    fake_pipeline(monkeypatch)
    for _ in range(2):
        new_user(conn, ["physical_ai"])
    conn.execute("insert into public.pipeline_runs (industry_id, run_date, triggered_by, status) "
                 "values ('physical_ai', current_date - 2, 'schedule', 'complete')")
    assert run_daily.run(conn, jobs=False, send_email=False)["industries"] == []   # 2 followers: weekly
    new_user(conn, ["physical_ai"])                                                # 3rd follower: daily
    assert [r["id"] for r in run_daily.run(conn, jobs=False, send_email=False)["industries"]] == ["physical_ai"]


def test_email_adds_focus_highlights(conn, monkeypatch, mail):
    url = fake_pipeline(monkeypatch)
    user, email = new_user(conn, ["physical_ai"], opt_in=True)
    other, other_email = new_user(conn, ["physical_ai"], opt_in=True)
    conn.execute("update public.subscriptions set focus = 'Humanoid seed funding' where user_id = %s", (user,))
    run_daily.run(conn, jobs=False)
    by_to = {m["to"]: m["body"] for m in mail}
    assert "For your focus: Humanoid seed funding" in by_to[email] and url in by_to[email]
    assert "For your focus" not in by_to[other_email]


def test_failed_industry_is_retried_then_alerted(conn, monkeypatch, mail):
    fake_pipeline(monkeypatch, fail=True)
    new_user(conn, ["physical_ai"])
    summary = run_daily.run(conn, jobs=False, send_email=False)
    assert summary["failed"] == ["physical_ai"]
    runs = conn.execute("select status from public.pipeline_runs where industry_id = 'physical_ai' "
                        "and run_date = %s", (TODAY,)).fetchall()
    assert [r["status"] for r in runs] == ["failed", "failed"]  # first try + one retry
    assert any("pipeline failed" in m["subject"] for m in mail)


def test_budget_warns_then_pauses_user_features(conn, mail):
    now = datetime.now(timezone.utc)
    conn.execute("delete from public.cache where key like 'budget_alert:%%' or key = 'spend_paused'")
    conn.execute("insert into public.usage_events (action, cost_usd) values ('llm:test', 250)")
    status = run_daily.check_budget(conn, now, budget=300)
    assert status["paused"] is False and len(mail) == 1 and "83%" in mail[0]["subject"]
    run_daily.check_budget(conn, now, budget=300)
    assert len(mail) == 1  # warned once per month

    conn.execute("insert into public.usage_events (action, cost_usd) values ('llm:test', 60)")
    assert run_daily.check_budget(conn, now, budget=300)["paused"] is True
    assert "paused" in mail[-1]["subject"]
    user, _ = new_user(conn)
    with pytest.raises(ApiError) as exc:
        limits.reserve(conn, user, limits.TAILOR_RESUME)
    assert exc.value.status == 503 and exc.value.code == "paused"


def test_job_refresh_uses_followers_role_types_and_postgres(conn, monkeypatch):
    user, _ = new_user(conn, ["physical_ai"])
    conn.execute("insert into public.job_preferences (user_id, role_types, custom_role_types) "
                 "values (%s, %s, %s)", (user, ["Product Management"], "BizOps, Strategy"))
    seen = {}

    def fake_collect(profile, functions):
        seen["functions"] = functions
        seen["store"] = store()
        store().upsert_posting({"url": "https://jobs.example.com/1", "provider": "web", "title": "PM, Robots",
                                "remote": 1}, profile.id)
        return {"postings": 1}
    monkeypatch.setattr(run_daily, "collect_jobs", fake_collect)
    industry = run_daily.industries_to_run(conn)[0]
    stats = run_daily.refresh_jobs(conn, industry)
    assert seen["functions"] == ["Product Management", "BizOps", "Strategy"]
    assert isinstance(seen["store"], PgStore) and stats["postings"] == 1
    posting = conn.execute("select remote from public.job_postings where url = 'https://jobs.example.com/1'").fetchone()
    assert posting["remote"] is True


def test_pg_store_job_postings_and_boards(conn):
    s = PgStore(conn)
    url = f"https://boards.greenhouse.io/acme/jobs/{uuid.uuid4().hex[:6]}"
    job_id = s.upsert_posting({"url": url, "provider": "greenhouse", "board_token": "acme-test",
                               "title": "Strategy Manager", "company": "Acme"}, "physical_ai")
    assert s.upsert_posting({"url": url, "provider": "greenhouse", "board_token": "acme-test",
                             "title": "Senior Strategy Manager"}, "healthcare_robotics") == job_id
    row = conn.execute("select title, company, active from public.job_postings where id = %s", (job_id,)).fetchone()
    assert row == {"title": "Senior Strategy Manager", "company": "Acme", "active": True}
    industries = {r["industry_id"] for r in conn.execute(
        "select industry_id from public.job_posting_industries where job_id = %s", (job_id,))}
    assert industries == {"physical_ai", "healthcare_robotics"}

    assert s.deactivate_missing("greenhouse", "acme-test", {url}) == 0
    assert s.deactivate_missing("greenhouse", "acme-test", set()) == 1
    s.refresh_posting(job_id, {"url": url + "-moved", "title": "Senior Strategy Manager"})
    assert conn.execute("select url from public.job_postings where id = %s", (job_id,)).fetchone()["url"] == url + "-moved"

    conn.execute("update public.job_postings set provider = 'web', active = true, "
                 "last_seen_at = now() - interval '2 days' where id = %s", (job_id,))
    assert [p["id"] for p in s.postings_to_recheck("physical_ai", 24, ("web",)) if p["id"] == job_id] == [job_id]

    s.upsert_board("greenhouse", "acme-test", "Acme")
    board = next(b for b in s.list_boards() if b["token"] == "acme-test")
    assert not s.board_is_fresh(board, 12)
    s.mark_board_fetched("greenhouse", "acme-test")
    board = next(b for b in s.list_boards() if b["token"] == "acme-test")
    assert s.board_is_fresh(board, 12)
    assert not s.was_board_checked("acme test co")
    s.mark_board_checked("acme test co")
    assert s.was_board_checked("acme test co")


def test_pg_store_runs_and_companies_match_sqlite_shapes(conn):
    s = PgStore(conn)
    runs = s.get_all_runs("healthcare_robotics")
    assert runs and isinstance(runs[0]["run_date"], str) and "briefing_text" in runs[0]
    assert any(r["briefing_text"] for r in runs)
    events = s.get_all_events(industry_id="healthcare_robotics", date_from="2026-09-01")
    assert events and all(e["date"] is None or e["date"] >= "2026-09-01" for e in events)
    cid = s.insert_company("Zeta Test Robotics", "zeta test robotics", "physical_ai")
    assert s.insert_company("Zeta Test Robotics", "zeta test robotics", "physical_ai") == cid
    s.touch_company(cid, "healthcare_robotics", "2026-10-05")
    s.touch_company(cid, None, "2026-09-01")
    company = next(c for c in s.list_companies("healthcare_robotics") if c["id"] == cid)
    assert set(company["industry_ids"]) == {"physical_ai", "healthcare_robotics"}
    assert (company["first_seen"], company["last_seen"]) == ("2026-09-01", "2026-10-05")
    with use_store(s):
        ranking = store().rank_companies(__import__("profiles.loader", fromlist=["load_profile"])
                                         .load_profile("healthcare_robotics"))
    assert ranking and ranking[0]["rank"] == 1
