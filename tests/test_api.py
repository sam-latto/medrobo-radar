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
    admin = FakeAdmin()
    app = create_app(db=SharedConnectionDb(conn), verifier=FakeVerifier(), admin=admin)

    def new_user(prefix="api-test"):
        user_id = uuid.uuid4()
        conn.execute(
            "insert into auth.users (id, instance_id, aud, role, email, raw_user_meta_data) "
            "values (%s, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', %s, %s)",
            (user_id, f"{prefix}-{user_id.hex[:8]}@umich.edu", json.dumps({"full_name": "Test User"})))
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
    c, user = env["client"], env["new_user"]()
    brief = c.get("/api/v1/briefs", headers=auth(user), params={"industry_id": "healthcare_robotics"}).json()
    assert brief["brief_date"] == "2026-09-29" and brief["content_md"] and brief["industry_name"]
    dates = c.get("/api/v1/briefs/dates", headers=auth(user), params={"industry_id": "healthcare_robotics"})
    assert dates.json()["dates"][:2] == ["2026-09-29", "2026-04-19"]
    r = c.get("/api/v1/briefs", headers=auth(user), params={"industry_id": "consulting"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


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
    assert items == sorted(items, key=lambda i: -i["score"]) or items[0]["score"] >= items[-1]["score"]
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
