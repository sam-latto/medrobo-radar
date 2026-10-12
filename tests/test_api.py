"""End-to-end API tests against the Supabase dev database.

Opt-in: RUN_DB_TESTS=1 python -m pytest tests/test_api.py
Each test runs inside one transaction that is rolled back (test users, rows and usage
included). Claude and Supabase Storage/Auth calls are replaced with fakes.
"""
import json
import os
import re
import uuid
from contextlib import contextmanager

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg.rows import dict_row  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.errors import ApiError  # noqa: E402
from api.main import create_app  # noqa: E402
from config import DATABASE_URL  # noqa: E402
from jobs import matcher  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1" or not DATABASE_URL,
    reason="live database tests; set RUN_DB_TESTS=1 and DATABASE_URL")

GOOGLE_APP_META = json.dumps({"provider": "google", "providers": ["google"]})  # sign-up via Google

RESUME_TEXT = ("Sam Example\nProduct manager with 6 years in medical devices and surgical robotics.\n"
               "- Led launch of a robotic surgery analytics product used by 40 hospitals\n"
               "- Built pricing strategy that grew revenue 25%\n") * 3


class SharedConnectionDb:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


class FakeVerifier:
    """The bearer token is the user's id."""

    def verify(self, token):
        if token == "bad":
            raise ApiError(401, "invalid_token", "Your session has expired. Please sign in again.")
        return {"sub": token}


class FakeAdmin:
    def __init__(self):
        self.uploaded, self.deleted, self.deleted_users = {}, [], []

    def upload_resume(self, path, data, content_type):
        self.uploaded[path] = data

    def delete_files(self, paths):
        self.deleted.extend(paths)

    def list_user_files(self, user_id):
        return [p for p in self.uploaded if p.startswith(user_id)]

    def delete_user(self, user_id):
        self.deleted_users.append(user_id)


@pytest.fixture
def env():
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    conn.execute("select 1")  # open the outer transaction now, so every write is rolled back at the end
    admin = FakeAdmin()
    app = create_app(db=SharedConnectionDb(conn), verifier=FakeVerifier(), admin=admin)

    def new_user(prefix="api-test"):
        user_id = uuid.uuid4()
        conn.execute(
            "insert into auth.users (id, instance_id, aud, role, email, raw_user_meta_data, raw_app_meta_data) "
            "values (%s, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', %s, %s, %s)",
            (user_id, f"{prefix}-{user_id.hex[:8]}@umich.edu", json.dumps({"full_name": "Test User"}), GOOGLE_APP_META))
        return str(user_id)

    try:
        with TestClient(app) as client:
            yield {"client": client, "conn": conn, "admin": admin, "new_user": new_user}
    finally:
        conn.rollback()
        conn.close()


def auth(user_id):
    return {"Authorization": f"Bearer {user_id}"}


def fake_scores(calls):
    def fake_call_tool(prompt, tool, max_tokens=4096, system=None):
        calls.append(tool["name"])
        ids = [int(i) for i in re.findall(r"\[job_id=(\d+)\]", prompt)]
        return {"scores": [{"job_id": i, "score": 70 + i % 25, "summary": "Good fit", "strengths": ["a"],
                            "gaps": ["b"], "level_fit": "match", "industry_match": True} for i in ids]}
    return fake_call_tool


def fake_tailoring(calls):
    def fake_call_tool(prompt, tool, max_tokens=4096, system=None):
        calls.append(tool["name"])
        return {"fit_overview": "Strong", "summary_rewrite": "PM", "keywords_to_add": [], "emphasize": [],
                "de_emphasize": [], "gaps": [], "cover_letter_angle": "Robots",
                "bullet_rewrites": [{"original": "Built pricing strategy that grew revenue 25%",
                                     "suggested": "x", "why": "y"},
                                    {"original": "Invented a time machine", "suggested": "x", "why": "y"}]}
    return fake_call_tool


def upload_resume(client, user, text=RESUME_TEXT, role_types=()):
    return client.post("/api/v1/resumes", headers=auth(user),
                       files={"file": ("resume.txt", text.encode(), "text/plain")},
                       data={"role_types": list(role_types)})


# ── Auth and account ─────────────────────────────────────────────────────────

def test_requests_need_a_valid_session(env):
    c = env["client"]
    r = c.get("/api/v1/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "not_signed_in"
    assert c.get("/api/v1/me", headers=auth("bad")).json()["error"]["code"] == "invalid_token"
    r = c.get("/api/v1/me", headers=auth(str(uuid.uuid4())))  # valid token, no umich profile
    assert r.status_code == 403 and r.json()["error"]["code"] == "account_not_allowed"
    assert c.get("/health").json() == {"ok": True}


def test_me_shows_profile_limits_and_settings(env):
    c, user = env["client"], env["new_user"]()
    me = c.get("/api/v1/me", headers=auth(user)).json()
    assert me["community"] == "ross" and me["email_brief_opt_in"] is False and me["subscriptions"] == []
    assert me["usage"]["find_jobs"]["max"] == 1 and me["usage"]["tailor_resume"]["max"] == 30
    me = c.patch("/api/v1/me", headers=auth(user), json={"email_brief_opt_in": True}).json()
    assert me["email_brief_opt_in"] is True


def test_export_and_delete_account(env):
    c, user, admin = env["client"], env["new_user"](), env["admin"]
    assert upload_resume(c, user).status_code == 201
    export = c.get("/api/v1/me/export", headers=auth(user))
    assert export.status_code == 200 and len(export.json()["resumes"]) == 1
    assert c.delete("/api/v1/me", headers=auth(user)).status_code == 204
    assert admin.deleted_users == [user] and admin.deleted and admin.deleted[0].startswith(user)
    assert c.get("/api/v1/me", headers=auth(user)).status_code == 403


# ── Industries ───────────────────────────────────────────────────────────────

def test_catalog_subscriptions_and_custom_requests(env):
    c, user = env["client"], env["new_user"]()
    sectors = c.get("/api/v1/industries", headers=auth(user)).json()["sectors"]
    flat = {i["id"]: i for s in sectors for i in [s, *s.get("industries", [])]}
    assert flat["consulting"]["followable"] and flat["healthcare_robotics"]["tracking_active"]
    assert not flat["healthcare"]["followable"]

    r = c.put("/api/v1/subscriptions", headers=auth(user),
              json={"industries": [{"industry_id": "physical_ai"}, {"industry_id": "healthcare"}]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_industry"  # sector isn't followable
    r = c.put("/api/v1/subscriptions", headers=auth(user),
              json={"industries": [{"industry_id": "physical_ai"}, {"industry_id": "consulting"}]})
    assert [s["industry_id"] for s in r.json()["subscriptions"]] == ["consulting", "physical_ai"]
    assert c.get("/api/v1/me", headers=auth(user)).json()["onboarded_at"] is not None

    r = c.post("/api/v1/industries/requests", headers=auth(user), json={"label": "surgical robotics"}).json()
    assert r["status"] == "focus" and r["matched_industry_id"] == "healthcare_robotics"
    subs = c.get("/api/v1/me", headers=auth(user)).json()["subscriptions"]
    assert {"industry_id": "healthcare_robotics", "name": "Healthcare robotics",
            "focus": "Surgical robotics"} in subs
    r = c.post("/api/v1/industries/requests", headers=auth(user), json={"label": "Underwater basket weaving"})
    assert r.json()["status"] == "pending_review"


# ── Briefs, events, companies ────────────────────────────────────────────────

def test_briefs(env):
    c, conn, user = env["client"], env["conn"], env["new_user"]()
    conn.execute("delete from public.briefs where industry_id = 'consulting'")
    r = c.get("/api/v1/briefs", headers=auth(user), params={"industry_id": "consulting"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    for day, text in (("2099-01-01", "# Older"), ("2099-01-02", "# Newest")):
        conn.execute("insert into public.briefs (industry_id, brief_date, headline, content_md) "
                     "values ('consulting', %s, %s, %s)", (day, text[2:], text))
    brief = c.get("/api/v1/briefs", headers=auth(user), params={"industry_id": "consulting"}).json()
    assert brief["brief_date"] == "2099-01-02" and brief["headline"] == "Newest" and brief["industry_name"]
    older = c.get("/api/v1/briefs", headers=auth(user), params={"industry_id": "consulting", "date": "2099-01-01"})
    assert older.json()["headline"] == "Older"
    dates = c.get("/api/v1/briefs/dates", headers=auth(user), params={"industry_id": "consulting"}).json()["dates"]
    assert dates == ["2099-01-02", "2099-01-01"]


def test_events_filters_paging_and_csv(env):
    c, user = env["client"], env["new_user"]()
    params = {"industry_id": "healthcare_robotics", "page_size": 5}
    page = c.get("/api/v1/events", headers=auth(user), params=params).json()
    assert page["total"] > 5 and len(page["items"]) == 5
    dates = [e["date"] for e in page["items"] if e["date"]]
    assert dates == sorted(dates, reverse=True)
    assert all(e["source_name"] and e["watching"] is False for e in page["items"])
    research = c.get("/api/v1/events", headers=auth(user),
                     params={**params, "type": "research", "page_size": 100}).json()
    assert research["items"] and {e["event_type"] for e in research["items"]} == {"research"}
    windowed = c.get("/api/v1/events", headers=auth(user),
                     params={**params, "from": "2026-09-01", "to": "2026-09-30", "page_size": 100}).json()
    assert all("2026-09-01" <= e["date"] <= "2026-09-30" for e in windowed["items"])
    # No industry given: only followed industries (none yet)
    assert c.get("/api/v1/events", headers=auth(user)).json()["total"] == 0
    csv = c.get("/api/v1/events.csv", headers=auth(user), params={"industry_id": "healthcare_robotics"})
    assert csv.headers["content-type"].startswith("text/csv")
    assert csv.text.splitlines()[0].startswith("date,industry,company")
    assert len(csv.text.splitlines()) == page["total"] + 1


def test_companies_ranking_profile_and_watchlist(env):
    c, user = env["client"], env["new_user"]()
    companies = c.get("/api/v1/companies", headers=auth(user), params={"industry_id": "healthcare_robotics"}).json()
    items = companies["items"]
    assert items and [i["rank"] for i in items] == list(range(1, len(items) + 1))
    scores = [i["score"] for i in items]
    assert scores == sorted(scores, reverse=True)
    top = items[0]
    assert c.put(f"/api/v1/watchlist/{top['company_id']}", headers=auth(user)).status_code == 204
    watched = c.get("/api/v1/companies", headers=auth(user),
                    params={"industry_id": "healthcare_robotics", "watching": True}).json()["items"]
    assert [i["company_id"] for i in watched] == [top["company_id"]] and watched[0]["watching"]

    profile = c.get(f"/api/v1/companies/{top['company_id']}", headers=auth(user)).json()
    assert profile["name"] == top["company"] and profile["watching"] and profile["events"]
    assert {"filings", "trials", "research", "hiring", "similar"} <= profile.keys()
    assert c.delete(f"/api/v1/watchlist/{top['company_id']}", headers=auth(user)).status_code == 204
    assert c.get("/api/v1/companies/999999999", headers=auth(user)).status_code == 404
    assert c.put("/api/v1/watchlist/999999999", headers=auth(user)).status_code == 404


# ── Jobs ─────────────────────────────────────────────────────────────────────

def test_resume_upload_validation_and_privacy(env):
    c, admin = env["client"], env["admin"]
    a, b = env["new_user"](), env["new_user"]()
    r = upload_resume(c, a, role_types=["Product Management"])
    assert r.status_code == 201 and r.json()["role_types"] == ["Product Management"]
    assert list(admin.uploaded)[0].startswith(f"{a}/")
    assert upload_resume(c, a, text="too short").json()["error"]["code"] == "unreadable_file"
    bad = c.post("/api/v1/resumes", headers=auth(a), files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert bad.json()["error"]["code"] == "unsupported_file"

    resume_id = r.json()["id"]
    assert c.get("/api/v1/resumes", headers=auth(b)).json()["items"] == []        # B can't see A's resume
    assert c.patch(f"/api/v1/resumes/{resume_id}", headers=auth(b), json={"role_types": []}).status_code == 404
    assert c.delete(f"/api/v1/resumes/{resume_id}", headers=auth(b)).status_code == 404
    assert c.delete(f"/api/v1/resumes/{resume_id}", headers=auth(a)).status_code == 204
    assert admin.deleted and admin.deleted[0].startswith(f"{a}/")


def test_find_and_score_runs_once_per_day(env, monkeypatch):
    c, user = env["client"], env["new_user"]()
    calls = []
    monkeypatch.setattr(matcher, "call_tool", fake_scores(calls))
    r = c.post("/api/v1/jobs/search", headers=auth(user))
    assert r.json()["error"]["code"] == "no_resume"
    upload_resume(c, user)
    prefs = c.put("/api/v1/job-preferences", headers=auth(user),
                  json={"role_types": ["Product Management"], "industry_ids": ["physical_ai", "healthcare_robotics"]})
    assert prefs.json()["role_types"] == ["Product Management"]

    r = c.post("/api/v1/jobs/search", headers=auth(user))
    assert r.status_code == 202
    task = c.get(f"/api/v1/tasks/{r.json()['task_id']}", headers=auth(user)).json()
    assert task["status"] == "complete", task
    assert task["result"]["scored"] > 0 and calls

    matches = c.get("/api/v1/jobs/matches", headers=auth(user)).json()
    assert matches["total"] == task["result"]["scored"]
    scores = [m["score"] for m in matches["items"]]
    assert scores == sorted(scores, reverse=True) and matches["items"][0]["title"]

    again = c.post("/api/v1/jobs/search", headers=auth(user))
    assert again.status_code == 429 and again.json()["error"]["code"] == "limit_reached"
    assert "1 job search per day" in again.json()["error"]["message"]
    other = env["new_user"]()
    assert c.get(f"/api/v1/tasks/{r.json()['task_id']}", headers=auth(other)).status_code == 404


def test_failed_search_is_not_counted(env, monkeypatch):
    c, user = env["client"], env["new_user"]()
    upload_resume(c, user)
    c.put("/api/v1/job-preferences", headers=auth(user),
          json={"role_types": ["Product Management"], "industry_ids": ["physical_ai"]})

    def boom(*a, **k):
        raise RuntimeError("database fell over")
    monkeypatch.setattr("api.routes.jobs._active_postings", boom)
    r = c.post("/api/v1/jobs/search", headers=auth(user))
    task = c.get(f"/api/v1/tasks/{r.json()['task_id']}", headers=auth(user)).json()
    assert task["status"] == "failed" and "wasn't counted" in task["error"]
    assert c.get("/api/v1/me", headers=auth(user)).json()["usage"]["find_jobs"]["used"] == 0


def test_tailoring_is_metered_cached_and_capped(env, monkeypatch):
    c, conn, user = env["client"], env["conn"], env["new_user"]()
    calls = []
    monkeypatch.setattr(matcher, "call_tool", fake_tailoring(calls))
    upload_resume(c, user)
    job_id = conn.execute("select id from public.job_postings where active order by id limit 1").fetchone()["id"]

    r = c.post(f"/api/v1/jobs/{job_id}/tailor", headers=auth(user))
    assert r.status_code == 202
    task = c.get(f"/api/v1/tasks/{r.json()['task_id']}", headers=auth(user)).json()
    assert task["status"] == "complete"
    rewrites = task["result"]["advice"]["bullet_rewrites"]
    assert [rw["verified"] for rw in rewrites] == [True, False]

    again = c.post(f"/api/v1/jobs/{job_id}/tailor", headers=auth(user)).json()
    assert again["status"] == "complete" and len(calls) == 1          # cached: no new Claude call
    saved = c.get(f"/api/v1/jobs/{job_id}/tailor", headers=auth(user)).json()
    assert saved["advice"]["fit_overview"] == "Strong"
    assert c.get("/api/v1/me", headers=auth(user)).json()["usage"]["tailor_resume"]["used"] == 1

    # Use up the month's allowance, then a new job is refused
    conn.execute("insert into public.usage_events (user_id, action) "
                 "select %s, 'tailor_resume' from generate_series(1, 29)", (user,))
    other_job = conn.execute("select id from public.job_postings where active and id <> %s order by id limit 1",
                             (job_id,)).fetchone()["id"]
    r = c.post(f"/api/v1/jobs/{other_job}/tailor", headers=auth(user))
    assert r.status_code == 429 and "30 resume tailorings per month" in r.json()["error"]["message"]
    assert c.get(f"/api/v1/jobs/{other_job}/tailor", headers=auth(user)).status_code == 404


def test_validation_errors_use_the_error_shape(env):
    c, user = env["client"], env["new_user"]()
    r = c.get("/api/v1/events", headers=auth(user), params={"page": 0})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    assert r.json()["error"]["details"][0]["field"] == "page"


def test_task_records_claude_tokens_and_cost(env, monkeypatch):
    from types import SimpleNamespace
    from pipeline import llm
    c, conn, user = env["client"], env["conn"], env["new_user"]()
    advice = fake_tailoring([])("", {"name": "tailor_resume"})
    response = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", input=advice)], stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=4000, output_tokens=1500, cache_creation_input_tokens=0,
                              cache_read_input_tokens=0))
    monkeypatch.setattr(llm.client.messages, "create", lambda **kw: response)
    monkeypatch.setattr(llm, "MODEL", "claude-sonnet-4-6")
    upload_resume(c, user)
    job_id = conn.execute("select id from public.job_postings where active order by id limit 1").fetchone()["id"]
    task = c.post(f"/api/v1/jobs/{job_id}/tailor", headers=auth(user)).json()
    assert c.get(f"/api/v1/tasks/{task['task_id']}", headers=auth(user)).json()["status"] == "complete"
    rows = conn.execute("select action, model, input_tokens, output_tokens, cost_usd from public.usage_events "
                        "where user_id = %s order by id", (user,)).fetchall()
    assert [r["action"] for r in rows] == ["tailor_resume", "llm:tailor_resume"]
    llm_row = rows[1]
    assert (llm_row["model"], llm_row["input_tokens"], llm_row["output_tokens"]) == ("claude-sonnet-4-6", 4000, 1500)
    assert float(llm_row["cost_usd"]) == pytest.approx(0.0345)  # 4000*$3/M + 1500*$15/M


def test_focus_highlights_events_and_brief(env):
    c, user = env["client"], env["new_user"]()
    c.put("/api/v1/subscriptions", headers=auth(user),
          json={"industries": [{"industry_id": "healthcare_robotics", "focus": "Surgical robotics"}]})
    params = {"industry_id": "healthcare_robotics", "page_size": 100}
    everything = c.get("/api/v1/events", headers=auth(user), params=params).json()
    focused = c.get("/api/v1/events", headers=auth(user), params={**params, "focus_only": True}).json()
    assert 0 < focused["total"] < everything["total"]
    assert all(e["matches_focus"] for e in focused["items"])
    assert {e["id"] for e in everything["items"] if e["matches_focus"]} == {e["id"] for e in focused["items"]}
    brief = c.get("/api/v1/briefs", headers=auth(user), params={"industry_id": "healthcare_robotics"}).json()
    assert brief["focus"] == "Surgical robotics" and brief["focus_events"]
    from api import focus as focus_mod
    stems = focus_mod.focus_stems("Surgical robotics", "Healthcare robotics")
    assert all(focus_mod.matches(e, stems) for e in brief["focus_events"])
    # Without a focus nothing is flagged
    other = env["new_user"]()
    items = c.get("/api/v1/events", headers=auth(other), params=params).json()["items"]
    assert not any(e["matches_focus"] for e in items)


def test_momentum_scores_come_from_recent_events(env):
    from datetime import date, timedelta
    c, conn, user = env["client"], env["conn"], env["new_user"]()
    run_id = conn.execute("insert into public.pipeline_runs (industry_id, run_date, triggered_by, status) "
                          "values ('healthcare_robotics', current_date, 'test', 'complete') returning id").fetchone()["id"]
    company = conn.execute("insert into public.companies (name, normalized_name) values ('Momentum Test Co', "
                           "'momentum test co') returning id").fetchone()["id"]
    conn.execute("insert into public.company_industries values (%s, 'healthcare_robotics')", (company,))
    recent = date.today() - timedelta(days=5)
    for n, (kind, amount) in enumerate([("funding", 50_000_000), ("funding", 50_000_000), ("regulatory", None)]):
        conn.execute("insert into public.events (run_id, industry_id, company_id, company, event_type, segment, date, "
                     "summary, source_url, funding_amount) values (%s, 'healthcare_robotics', %s, 'Momentum Test Co', "
                     "%s, 'surgical', %s, 's', %s, %s)", (run_id, company, kind, recent, f"https://x/{n}", amount))
    items = c.get("/api/v1/companies", headers=auth(user), params={"industry_id": "healthcare_robotics"}).json()["items"]
    row = next(i for i in items if i["company_id"] == company)
    assert row["score"] > 0 and row["events_90d"] == 3
    assert row["funding_90d"] == 50_000_000  # the same round reported twice counts once
    assert row["regulatory_90d"] == 1


# ── Review fixes ─────────────────────────────────────────────────────────────

def _scored_search(env, monkeypatch, user):
    monkeypatch.setattr(matcher, "call_tool", fake_scores([]))
    upload_resume(env["client"], user)
    env["client"].put("/api/v1/job-preferences", headers=auth(user),
                      json={"role_types": ["Product Management"], "industry_ids": ["physical_ai", "healthcare_robotics"]})
    task = env["client"].post("/api/v1/jobs/search", headers=auth(user)).json()
    assert env["client"].get(f"/api/v1/tasks/{task['task_id']}", headers=auth(user)).json()["status"] == "complete"


def test_matches_filters_and_privacy(env, monkeypatch):
    c, conn = env["client"], env["conn"]
    a, b = env["new_user"](), env["new_user"]()
    _scored_search(env, monkeypatch, a)
    everything = c.get("/api/v1/jobs/matches", headers=auth(a), params={"page_size": 100}).json()
    assert everything["total"] > 1
    closed = everything["items"][0]["job_id"]
    conn.execute("update public.job_postings set active = false where id = %s", (closed,))
    open_only = c.get("/api/v1/jobs/matches", headers=auth(a), params={"page_size": 100}).json()
    assert closed not in {m["job_id"] for m in open_only["items"]}
    with_closed = c.get("/api/v1/jobs/matches", headers=auth(a), params={"page_size": 100, "include_closed": True})
    assert closed in {m["job_id"] for m in with_closed.json()["items"]}
    top = max(m["score"] for m in everything["items"])
    high = c.get("/api/v1/jobs/matches", headers=auth(a),
                 params={"min_score": top, "include_closed": True}).json()["items"]
    assert high and all(m["score"] == top for m in high)

    # Another student sees none of it, and can't tailor with A's resume
    assert c.get("/api/v1/jobs/matches", headers=auth(b)).json()["total"] == 0
    a_resume = c.get("/api/v1/resumes", headers=auth(a)).json()["items"][0]["id"]
    upload_resume(c, b)
    r = c.post(f"/api/v1/jobs/{closed}/tailor", headers=auth(b), json={"resume_id": a_resume})
    assert r.json()["error"]["code"] == "no_resume"


def test_search_falls_back_to_followed_industries_and_explains_missing_setup(env, monkeypatch):
    c, user = env["client"], env["new_user"]()
    monkeypatch.setattr(matcher, "call_tool", fake_scores([]))
    upload_resume(c, user)
    assert c.post("/api/v1/jobs/search", headers=auth(user)).json()["error"]["code"] == "no_role_types"
    c.put("/api/v1/job-preferences", headers=auth(user), json={"role_types": ["Product Management"]})
    assert c.post("/api/v1/jobs/search", headers=auth(user)).json()["error"]["code"] == "no_industries"
    c.put("/api/v1/subscriptions", headers=auth(user), json={"industries": [{"industry_id": "physical_ai"}]})
    task = c.post("/api/v1/jobs/search", headers=auth(user)).json()
    assert c.get(f"/api/v1/tasks/{task['task_id']}", headers=auth(user)).json()["result"]["scored"] > 0


def test_failure_after_paid_work_still_counts(env, monkeypatch):
    from api.routes import jobs as job_routes
    from pipeline.llm import usage_sink
    c, user = env["client"], env["new_user"]()
    upload_resume(c, user)
    c.put("/api/v1/job-preferences", headers=auth(user),
          json={"role_types": ["Product Management"], "industry_ids": ["physical_ai"]})

    def work_that_spends_then_fails(*a):
        def work(conn, progress):
            usage_sink.get()({"purpose": "score_jobs", "model": "claude-sonnet-4-6", "input_tokens": 10,
                              "output_tokens": 1, "cache_creation_tokens": 0, "cache_read_tokens": 0,
                              "cost_usd": 0.01})
            raise RuntimeError("resume deleted mid-search")
        return work
    monkeypatch.setattr(job_routes, "find_and_score", work_that_spends_then_fails)
    task = c.post("/api/v1/jobs/search", headers=auth(user)).json()
    status = c.get(f"/api/v1/tasks/{task['task_id']}", headers=auth(user)).json()
    assert status["status"] == "failed" and "still counts" in status["error"]
    assert c.get("/api/v1/me", headers=auth(user)).json()["usage"]["find_jobs"]["used"] == 1


def test_double_click_returns_the_running_task(env):
    c, conn, user = env["client"], env["conn"], env["new_user"]()
    upload_resume(c, user)
    job_id = conn.execute("select id from public.job_postings where active order by id limit 1").fetchone()["id"]
    resume_id = c.get("/api/v1/resumes", headers=auth(user)).json()["items"][0]["id"]
    running = conn.execute("insert into public.tasks (user_id, kind, status, ref) values (%s, 'tailor_resume', "
                           "'running', %s) returning id", (user, f"tailor:{job_id}:{resume_id}")).fetchone()["id"]
    r = c.post(f"/api/v1/jobs/{job_id}/tailor", headers=auth(user)).json()
    assert r["task_id"] == str(running)
    assert c.get("/api/v1/me", headers=auth(user)).json()["usage"]["tailor_resume"]["used"] == 0


def test_stuck_tasks_are_failed_and_refunded(env):
    from api.tasks import sweep_stale_tasks
    conn, user = env["conn"], env["new_user"]()
    usage_id = conn.execute("insert into public.usage_events (user_id, action) values (%s, 'find_jobs') "
                            "returning id", (user,)).fetchone()["id"]
    task_id = conn.execute("insert into public.tasks (user_id, kind, status, usage_id, updated_at) values "
                           "(%s, 'find_jobs', 'running', %s, now() - interval '2 hours') returning id",
                           (user, usage_id)).fetchone()["id"]
    assert sweep_stale_tasks(conn) >= 1
    assert conn.execute("select status from public.tasks where id = %s", (task_id,)).fetchone()["status"] == "failed"
    assert conn.execute("select 1 from public.usage_events where id = %s", (usage_id,)).fetchone() is None


def test_budget_pause_and_its_expiry(env):
    c, conn, user = env["client"], env["conn"], env["new_user"]()
    upload_resume(c, user)
    job_id = conn.execute("select id from public.job_postings where active order by id limit 1").fetchone()["id"]
    conn.execute("delete from public.cache where key = 'spend_paused'")
    conn.execute("insert into public.cache (key, value, expires_at) values ('spend_paused', '{}', now() + interval '1 day')")
    r = c.post(f"/api/v1/jobs/{job_id}/tailor", headers=auth(user))
    assert r.status_code == 503 and r.json()["error"]["code"] == "paused"
    conn.execute("update public.cache set expires_at = now() - interval '1 minute' where key = 'spend_paused'")
    from jobs import matcher as m
    m_call = m.call_tool
    try:
        m.call_tool = fake_tailoring([])
        assert c.post(f"/api/v1/jobs/{job_id}/tailor", headers=auth(user)).status_code == 202
    finally:
        m.call_tool = m_call


def test_upload_limits_and_cleanup(env, monkeypatch):
    c, admin, user = env["client"], env["admin"], env["new_user"]()
    big = c.post("/api/v1/resumes", headers=auth(user),
                 files={"file": ("cv.txt", b"x" * (5 * 1024 * 1024 + 1), "text/plain")})
    assert big.json()["error"]["code"] == "file_too_large"
    for _ in range(5):
        assert upload_resume(c, user).status_code == 201
    assert upload_resume(c, user).json()["error"]["code"] == "too_many_resumes"
    assert len(admin.uploaded) == 5


def test_delete_account_fails_safely_when_files_cannot_be_removed(env):
    c, admin, user = env["client"], env["admin"], env["new_user"]()
    upload_resume(c, user)
    admin.delete_files = lambda paths: (_ for _ in ()).throw(RuntimeError("storage down"))
    r = c.delete("/api/v1/me", headers=auth(user))
    assert r.status_code == 502 and r.json()["error"]["code"] == "delete_failed"
    assert admin.deleted_users == [] and c.get("/api/v1/me", headers=auth(user)).status_code == 200


def test_small_input_errors_are_422_not_500(env):
    c, user = env["client"], env["new_user"]()
    r = c.patch("/api/v1/me", headers=auth(user), json={"email_brief_opt_in": None})
    assert r.status_code == 422
    r = c.post("/api/v1/industries/requests", headers=auth(user), json={"label": "   "})
    assert r.status_code == 422
    r = c.put("/api/v1/job-preferences", headers=auth(user), json={"role_types": ["x" * 101]})
    assert r.status_code == 422


def test_industry_request_keeps_existing_focus_and_cap(env):
    c, conn, user = env["client"], env["conn"], env["new_user"]()
    c.put("/api/v1/subscriptions", headers=auth(user),
          json={"industries": [{"industry_id": "healthcare_robotics", "focus": "Exoskeletons"}]})
    c.post("/api/v1/industries/requests", headers=auth(user), json={"label": "Surgical robotics"})
    subs = {s["industry_id"]: s["focus"] for s in c.get("/api/v1/me", headers=auth(user)).json()["subscriptions"]}
    assert subs["healthcare_robotics"] == "Exoskeletons"
    ids = [r["id"] for r in conn.execute("select id from public.industries where kind = 'industry' "
                                         "and id <> 'private_equity' limit 25")]
    assert len(ids) == 25
    put = c.put("/api/v1/subscriptions", headers=auth(user), json={"industries": [{"industry_id": i} for i in ids]})
    assert put.status_code == 200, put.json()
    r = c.post("/api/v1/industries/requests", headers=auth(user), json={"label": "Private equity buyouts"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "too_many_industries", r.json()
