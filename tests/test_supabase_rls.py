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

PRIVATE_TABLES = ["subscriptions", "watchlist", "resumes", "job_preferences", "usage_events"]


@pytest.fixture
def db():
    conn = psycopg.connect(DATABASE_URL)
    conn.execute("select 1")  # open the outer transaction now, so every write is rolled back at the end
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _new_auth_user(conn, email: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    conn.execute(
        "insert into auth.users (id, instance_id, aud, role, email, raw_user_meta_data) "
        "values (%s, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', %s, %s)",
        (user_id, email, json.dumps({"full_name": email.split("@")[0]})))
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
    company_id = conn.execute("select id from public.companies order by id limit 1").fetchone()[0]
    for user in (a, b):
        conn.execute("insert into public.subscriptions (user_id, industry_id) values (%s, 'physical_ai')", (user,))
        conn.execute("insert into public.watchlist (user_id, company_id) values (%s, %s)", (user, company_id))
        conn.execute("insert into public.resumes (user_id, filename, text) values (%s, 'cv.pdf', 'text')", (user,))
        conn.execute("insert into public.job_preferences (user_id) values (%s)", (user,))
        conn.execute("insert into public.usage_events (user_id, action) values (%s, 'tailor_resume')", (user,))
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
