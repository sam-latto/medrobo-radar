"""Postgres-backed stores that the existing jobs/ code writes through.

Used from background tasks on a service connection, so every query filters by the
task's user id explicitly.
"""
from uuid import UUID

from psycopg.types.json import Jsonb

from api.db import as_service


class PgJobsStore:
    """Implements what jobs.matcher needs: get_matches, save_match, update_posting_company,
    get_advice, save_advice."""

    def __init__(self, conn, user_id: UUID):
        self.conn, self.user_id = conn, user_id

    def get_matches(self, resume_id) -> dict[int, dict]:
        with as_service(self.conn):
            rows = self.conn.execute(
                "select * from public.job_matches where user_id = %s and resume_id = %s",
                (self.user_id, resume_id)).fetchall()
        return {r["job_id"]: r for r in rows}

    def save_match(self, job_id: int, resume_id, score: int, summary: str, strengths: list, gaps: list,
                   level_fit: str | None, industry_match: bool | None = None) -> None:
        with as_service(self.conn):
            self.conn.execute(
                """insert into public.job_matches (user_id, job_id, resume_id, score, summary, strengths,
                       gaps, level_fit, industry_match)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   on conflict (user_id, job_id, resume_id) do update set score = excluded.score,
                     summary = excluded.summary, strengths = excluded.strengths, gaps = excluded.gaps,
                     level_fit = excluded.level_fit, industry_match = excluded.industry_match,
                     created_at = now()""",
                (self.user_id, job_id, resume_id, score, summary, Jsonb(strengths), Jsonb(gaps),
                 level_fit, industry_match))

    def update_posting_company(self, job_id: int, company: str) -> None:
        with as_service(self.conn):
            self.conn.execute(
                "update public.job_postings set company = %s where id = %s and (company is null or company = '')",
                (company, job_id))

    def get_advice(self, resume_id, kind: str, key: str) -> dict | None:
        with as_service(self.conn):
            row = self.conn.execute(
                "select content from public.resume_advice where user_id = %s and resume_id = %s "
                "and kind = %s and key = %s", (self.user_id, resume_id, kind, key)).fetchone()
        return row["content"] if row else None

    def save_advice(self, resume_id, kind: str, key: str, content: dict) -> None:
        with as_service(self.conn):
            self.conn.execute(
                """insert into public.resume_advice (user_id, resume_id, kind, key, content)
                   values (%s, %s, %s, %s, %s)
                   on conflict (user_id, resume_id, kind, key) do update set content = excluded.content,
                     created_at = now()""",
                (self.user_id, resume_id, kind, key, Jsonb(content)))


class PgKeywordCache:
    """Shared cache for role-type title keywords (jobs.functions.set_keyword_cache)."""

    def __init__(self, db):
        self.db = db

    def get(self, key: str):
        with self.db.connection() as conn, as_service(conn):
            row = conn.execute(
                "select value from public.cache where key = %s and (expires_at is null or expires_at > now())",
                (key,)).fetchone()
        return row["value"] if row else None

    def set(self, key: str, value) -> None:
        with self.db.connection() as conn, as_service(conn):
            conn.execute(
                "insert into public.cache (key, value) values (%s, %s) "
                "on conflict (key) do update set value = excluded.value, updated_at = now()",
                (key, Jsonb(value)))
