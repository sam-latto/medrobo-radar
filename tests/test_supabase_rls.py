"""Live checks of sign-in rules and row-level security against the Supabase dev database.

Opt-in: RUN_DB_TESTS=1 python -m pytest tests/test_supabase_rls.py
Everything runs inside one transaction that is rolled back, so no test users or rows
are left behind. Never point DATABASE_URL at production when running these.
"""
import json
import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from config import DATABASE_URL  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1" or not DATABASE_URL,
    reason="live database tests; set RUN_DB_TESTS=1 and DATABASE_URL")

GOOGLE_APP_META = json.dumps({"provider": "google", "providers": ["google"]})  # sign-up via Google

PRIVATE_TABLES = ["subscriptions", "watchlist", "resumes", "job_preferences", "usage_events", "job_matches",
                  "resume_advice", "custom_industry_requests", "tasks"]


@pytest.fixture
def db():
    conn = psycopg.connect(DATABASE_URL)
    conn.execute("select 1")  # open the outer transaction now, so every write is rolled back at the end
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _new_auth_user(conn, email: str, app_meta: str = GOOGLE_APP_META, user_meta: dict | None = None) -> uuid.UUID:
    user_id = uuid.uuid4()
    conn.execute(
        "insert into auth.users (id, instance_id, aud, role, email, raw_user_meta_data, raw_app_meta_data) "
        "values (%s, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', %s, %s, %s)",
        (user_id, email, json.dumps(user_meta if user_meta is not None else {"full_name": email.split("@")[0]}),
         app_meta))
    return user_id


def _act_as(conn, user_id: uuid.UUID | None):
    """Switch this transaction to a signed-in user (or anonymous when None)."""
    if user_id is None:
        conn.execute("set local role anon")
        conn.execute("select set_config('request.jwt.claims', '{\"role\": \"anon\"}', true)")
        return
    conn.execute("set local role authenticated")
    conn.execute("select set_config('request.jwt.claims', %s, true)",
                 (json.dumps({"sub": str(user_id), "role": "authenticated"}),))


def _setup_two_users(conn):
    a = _new_auth_user(conn, f"rls-test-a-{uuid.uuid4().hex[:8]}@umich.edu")
    b = _new_auth_user(conn, f"rls-test-b-{uuid.uuid4().hex[:8]}@umich.edu")
    # Own rows inside the rolled-back transaction, so the test doesn't depend on existing data
    company_id = conn.execute("insert into public.companies (name, normalized_name) values ('RLS Co', %s) "
                              "returning id", (f"rls co {uuid.uuid4().hex}",)).fetchone()[0]
    job_id = conn.execute("insert into public.job_postings (url, provider, title) values (%s, 'web', 'PM') "
                          "returning id", (f"https://jobs.example.com/{uuid.uuid4().hex}",)).fetchone()[0]
    for user in (a, b):
        conn.execute("insert into public.subscriptions (user_id, industry_id) values (%s, 'physical_ai')", (user,))
        conn.execute("insert into public.watchlist (user_id, company_id) values (%s, %s)", (user, company_id))
        resume_id = conn.execute("insert into public.resumes (user_id, filename, text) "
                                 "values (%s, 'cv.pdf', 'text') returning id", (user,)).fetchone()[0]
        conn.execute("insert into public.job_preferences (user_id) values (%s)", (user,))
        conn.execute("insert into public.usage_events (user_id, action) values (%s, 'tailor_resume')", (user,))
        conn.execute("insert into public.job_matches (user_id, job_id, resume_id, score) values (%s, %s, %s, 80)",
                     (user, job_id, resume_id))
        conn.execute("insert into public.resume_advice (user_id, resume_id, kind, key, content) "
                     "values (%s, %s, 'job', %s, '{}')", (user, resume_id, str(job_id)))
        conn.execute("insert into public.custom_industry_requests (user_id, label) values (%s, 'Space')", (user,))
        conn.execute("insert into public.tasks (user_id, kind) values (%s, 'find_jobs')", (user,))
    return a, b


def test_new_umich_user_gets_profile_in_ross(db):
    user = _new_auth_user(db, f"rls-test-{uuid.uuid4().hex[:8]}@UMICH.edu")
    row = db.execute(
        "select p.email, c.slug, p.email_brief_opt_in from public.profiles p "
        "join public.communities c on c.id = p.community_id where p.id = %s", (user,)).fetchone()
    assert row is not None
    assert row[0].endswith("@umich.edu") and row[1] == "ross" and row[2] is False


def test_non_umich_email_is_rejected(db):
    with pytest.raises(psycopg.errors.RaiseException, match="limited to approved school accounts"):
        _new_auth_user(db, f"someone-{uuid.uuid4().hex[:8]}@gmail.com")


def test_user_sees_only_own_private_rows(db):
    a, b = _setup_two_users(db)
    _act_as(db, a)
    for table in PRIVATE_TABLES:
        owners = {r[0] for r in db.execute(f"select user_id from public.{table}")}
        assert owners == {a}, f"{table} leaked rows: {owners}"
    assert [r[0] for r in db.execute("select id from public.profiles")] == [a]


def test_user_cannot_write_rows_for_someone_else(db):
    a, b = _setup_two_users(db)
    _act_as(db, a)
    with db.transaction():  # savepoint
        db.execute("insert into public.resumes (user_id, text) values (%s, 'mine')", (a,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.transaction():
            db.execute("insert into public.resumes (user_id, text) values (%s, 'not mine')", (b,))
    assert db.execute("update public.job_preferences set remote_only = true where user_id = %s",
                      (b,)).rowcount == 0
    assert db.execute("delete from public.watchlist where user_id = %s", (b,)).rowcount == 0


def test_shared_data_is_readable_but_not_writable(db):
    a, _ = _setup_two_users(db)
    _act_as(db, a)
    assert db.execute("select count(*) from public.industries").fetchone()[0] > 0
    db.execute("select count(*) from public.events").fetchone()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.transaction():
            db.execute("update public.industries set name = name")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.transaction():
            db.execute("insert into public.usage_events (user_id, action) values (%s, 'find_jobs')", (a,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.transaction():
            db.execute("select * from public.cache")


def test_profile_settings_editable_but_not_plan_or_community(db):
    a, b = _setup_two_users(db)
    _act_as(db, a)
    assert db.execute("update public.profiles set email_brief_opt_in = true where id = %s",
                      (a,)).rowcount == 1
    assert db.execute("update public.profiles set email_brief_opt_in = true where id = %s",
                      (b,)).rowcount == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.transaction():
            db.execute("update public.profiles set plan_id = null where id = %s", (a,))


def test_anonymous_visitors_read_nothing(db):
    _act_as(db, None)
    for table in ["events", "briefs", "industries", "profiles", "resumes"]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with db.transaction():
                db.execute(f"select 1 from public.{table} limit 1")


# ── Sign-in rules ────────────────────────────────────────────────────────────

def test_profile_keeps_display_name(db):
    user = _new_auth_user(db, f"rls-test-{uuid.uuid4().hex[:8]}@umich.edu", user_meta={"name": "Sam Test"})
    assert db.execute("select display_name from public.profiles where id = %s", (user,)).fetchone()[0] == "Sam Test"


@pytest.mark.parametrize("email, app_meta", [
    ("x@med.umich.edu", GOOGLE_APP_META),                       # a subdomain isn't the community's domain
    ("no-at-sign", GOOGLE_APP_META),
    ("student@umich.edu", json.dumps({"provider": "email", "providers": ["email"]})),  # only Google sign-in
])
def test_other_sign_ins_are_rejected(db, email, app_meta):
    with pytest.raises(psycopg.errors.RaiseException):
        with db.transaction():
            _new_auth_user(db, email, app_meta=app_meta)


# ── Limits the database enforces (whatever writes the row) ───────────────────

@pytest.mark.parametrize("sql", [
    "update public.job_preferences set role_types = array_fill('x'::text, array[21]) where user_id = %(a)s",
    "update public.job_preferences set custom_role_types = repeat('x', 301) where user_id = %(a)s",
    "update public.job_preferences set role_types = array[repeat('x', 101)] where user_id = %(a)s",
    "update public.resumes set storage_path = %(b)s || '/stolen.pdf' where user_id = %(a)s",
    "update public.resumes set text = repeat('x', 30001) where user_id = %(a)s",
    "update public.subscriptions set focus = repeat('x', 121) where user_id = %(a)s",
])
def test_database_limits_reject_oversized_rows(db, sql):
    a, b = _setup_two_users(db)
    _act_as(db, a)
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.transaction():
            db.execute(sql, {"a": a, "b": str(b)})


def test_row_count_limits(db):
    a, _ = _setup_two_users(db)
    _act_as(db, a)
    for _ in range(4):  # one resume already exists: 5 is the limit
        db.execute("insert into public.resumes (user_id, text) values (%s, 'cv')", (a,))
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.transaction():
            db.execute("insert into public.resumes (user_id, text) values (%s, 'cv')", (a,))
    db.execute("delete from public.subscriptions where user_id = %s", (a,))
    industries = [r[0] for r in db.execute("select id from public.industries where kind = 'industry' limit 26")]
    for industry_id in industries[:25]:
        db.execute("insert into public.subscriptions (user_id, industry_id) values (%s, %s)", (a, industry_id))
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.transaction():
            db.execute("insert into public.subscriptions (user_id, industry_id) values (%s, %s)", (a, industries[25]))


# ── More access checks ───────────────────────────────────────────────────────

def test_users_cannot_change_identity_or_reach_worker_data(db):
    a, _ = _setup_two_users(db)
    other_community = db.execute("insert into public.communities (slug, name, email_domain, plan_id) "
                                 "select 'test-c', 'Test', 'test.edu', id from public.plans limit 1 "
                                 "returning id").fetchone()[0]
    _act_as(db, a)
    for sql, params in [
        ("update public.profiles set community_id = %s where id = %s", (other_community, a)),
        ("update public.profiles set email = 'x@umich.edu' where id = %s", (a,)),
        ("delete from public.profiles where id = %s", (a,)),
        ("insert into public.events (run_id, industry_id, event_type, segment, summary, source_url) "
         "values (gen_random_uuid(), 'physical_ai', 'news', 'other', 'x', 'https://x')", None),
        ("select 1 from public.pipeline_runs limit 1", None),  # its error details can hold upstream URLs
    ]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with db.transaction():
                db.execute(sql, params)


def test_resume_files_are_private_per_user(db):
    a, b = _setup_two_users(db)
    for user in (a, b):
        db.execute("insert into storage.objects (bucket_id, name, owner) values ('resumes', %s, %s)",
                   (f"{user}/cv.pdf", user))
    _act_as(db, a)
    names = [r[0] for r in db.execute("select name from storage.objects where bucket_id = 'resumes'")]
    assert names == [f"{a}/cv.pdf"]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.transaction():
            db.execute("insert into storage.objects (bucket_id, name, owner) values ('resumes', %s, %s)",
                       (f"{b}/planted.pdf", a))


def test_rerunning_the_seed_keeps_industry_status_and_profiles(db):
    from pathlib import Path
    db.execute("update public.industries set status = 'active', profile_yaml = 'x' where id = 'consulting'")
    db.execute((Path(__file__).resolve().parent.parent / "supabase" / "seed.sql").read_text(encoding="utf-8"))
    row = db.execute("select status, profile_yaml from public.industries where id = 'consulting'").fetchone()
    assert row == ("active", "x")


# ── Option A guard: Supabase's direct REST access to our tables stays off ────

def test_supabase_rest_access_to_tables_is_off():
    """The API is the only way in (docs/ARCHITECTURE.md). If someone re-enables the Data API
    for the public schema, requests reach the tables again; this fails so it can't happen silently."""
    import urllib.error
    import urllib.request
    from config import SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL
    request = urllib.request.Request(f"{SUPABASE_URL}/rest/v1/industries?select=id&limit=1",
                                     headers={"apikey": SUPABASE_PUBLISHABLE_KEY})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status, body = response.status, response.read().decode()
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read().decode()
    # 42501 means the request reached the table and was refused only by its permissions
    assert status != 200 and '"42501"' not in body, f"REST access to public tables is on: {status} {body[:200]}"
