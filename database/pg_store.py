"""Postgres versions of the data functions the pipeline and job collector use.

Same names and return shapes as the SQLite functions in database.db, database.companies
and database.jobs_db, so the pipeline code runs unchanged under use_store(PgStore(conn)).
Dates go back out as ISO strings, like SQLite returns them.

The job collector calls these from several threads; one lock serializes access to the
shared connection (each call is short next to the HTTP work around it).
"""
import threading
from datetime import date, datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from pipeline.models import normalize_date

POSTING_FIELDS = ("provider", "board_token", "company", "title", "location", "remote", "department",
                  "employment_type", "compensation", "description", "posted_at")


def _iso(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _plain(row: dict | None) -> dict | None:
    return None if row is None else {k: _iso(v) for k, v in row.items()}


def _remote(value):
    return None if value is None else bool(value)


def _is_real_brief(text: str | None) -> bool:
    return bool(text) and not text.startswith("No ")


def brief_headline(markdown: str) -> str | None:
    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip() or None
    return None


class PgStore:
    def __init__(self, conn):
        self.conn = conn
        self._lock = threading.RLock()

    def _q(self, sql: str, params=None):
        with self._lock, self.conn.transaction():
            return self.conn.execute(sql, params)

    def _one(self, sql: str, params=None) -> dict | None:
        with self._lock, self.conn.transaction():
            return self.conn.execute(sql, params).fetchone()

    def _all(self, sql: str, params=None) -> list[dict]:
        with self._lock, self.conn.transaction():
            return self.conn.execute(sql, params).fetchall()

    # ── Runs, briefs, events ─────────────────────────────────────────────────

    def url_exists(self, url: str, industry_id: str) -> bool:
        return self._one("select 1 from public.events where industry_id = %s and source_url = %s",
                         (industry_id, url)) is not None

    def insert_run(self, run_id: str, run_date: str, triggered_by: str, industry_id: str) -> None:
        self._q("insert into public.pipeline_runs (id, industry_id, run_date, triggered_by, status) "
                "values (%s, %s, %s, %s, 'running')", (run_id, industry_id, run_date, triggered_by))

    def update_run(self, run_id: str, status: str, briefing_text: str = None, source_counts: dict = None) -> None:
        with self._lock, self.conn.transaction():
            run = self.conn.execute(
                "update public.pipeline_runs set status = %s, source_counts = coalesce(%s, source_counts), "
                "completed_at = now() where id = %s returning industry_id, run_date",
                (status, Jsonb(source_counts) if source_counts is not None else None, run_id)).fetchone()
            if run and status == "complete" and _is_real_brief(briefing_text):
                self.conn.execute(
                    """insert into public.briefs (industry_id, run_id, brief_date, headline, content_md)
                       values (%s, %s, %s, %s, %s)
                       on conflict (industry_id, brief_date) do update set run_id = excluded.run_id,
                         headline = excluded.headline, content_md = excluded.content_md, created_at = now()""",
                    (run["industry_id"], run_id, run["run_date"], brief_headline(briefing_text), briefing_text))

    def set_run_cost(self, run_id: str, cost_usd: float) -> None:
        self._q("update public.pipeline_runs set cost_usd = %s where id = %s", (cost_usd, run_id))

    def insert_event(self, run_id: str, event: dict) -> int | None:
        row = self._one(
            """insert into public.events (run_id, industry_id, company, company_id, product, event_type, segment,
                   date, summary, source_url, source_type, funding_amount, regulatory_status)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (industry_id, source_url) do nothing returning id""",
            (run_id, event["industry_id"], event.get("company"), event.get("company_id"), event.get("product"),
             event["event_type"], event["segment"], normalize_date(event.get("date")), event["summary"],
             event["source_url"], event.get("source_type") or "tavily", event.get("funding_amount"),
             event.get("regulatory_status")))
        return row["id"] if row else None

    def get_all_runs(self, industry_id: str = None) -> list[dict]:
        rows = self._all(
            """select r.id::text as id, r.industry_id, r.run_date, r.triggered_by, r.status, r.source_counts,
                      r.completed_at, b.content_md as briefing_text
               from public.pipeline_runs r left join public.briefs b on b.run_id = r.id
               where %(industry)s::text is null or r.industry_id = %(industry)s
               order by r.run_date desc, r.completed_at desc nulls last""", {"industry": industry_id})
        return [_plain(r) for r in rows]

    def get_all_events(self, industry_id: str = None, date_from: str = None, **_ignored) -> list[dict]:
        rows = self._all(
            """select * from public.events
               where (%(industry)s::text is null or industry_id = %(industry)s)
                 and (%(from)s::date is null or date >= %(from)s)
               order by created_at desc""", {"industry": industry_id, "from": date_from})
        return [_plain(r) for r in rows]

    # ── Companies ────────────────────────────────────────────────────────────

    def list_companies(self, industry_id: str | None = None) -> list[dict]:
        rows = self._all(
            """select c.*, coalesce(array_agg(ci.industry_id) filter (where ci.industry_id is not null), '{}')
                      as industry_ids
               from public.companies c left join public.company_industries ci on ci.company_id = c.id
               group by c.id
               having %(industry)s::text is null or %(industry)s = any(array_agg(ci.industry_id))
               order by c.name""", {"industry": industry_id})
        return [_plain(r) for r in rows]

    def insert_company(self, name: str, normalized_name: str, industry_id: str | None = None) -> int:
        with self._lock, self.conn.transaction():
            row = self.conn.execute(
                "insert into public.companies (name, normalized_name) values (%s, %s) "
                "on conflict (normalized_name) do nothing returning id", (name, normalized_name)).fetchone()
            company_id = row["id"] if row else self.conn.execute(
                "select id from public.companies where normalized_name = %s", (normalized_name,)).fetchone()["id"]
            if industry_id and row:
                self.conn.execute("insert into public.company_industries (company_id, industry_id) "
                                  "values (%s, %s) on conflict do nothing", (company_id, industry_id))
        return company_id

    def touch_company(self, company_id: int, industry_id: str | None, seen_date: str | None) -> None:
        seen = normalize_date(seen_date)
        with self._lock, self.conn.transaction():
            if industry_id:
                self.conn.execute("insert into public.company_industries (company_id, industry_id) "
                                  "values (%s, %s) on conflict do nothing", (company_id, industry_id))
            if seen:
                self.conn.execute(
                    "update public.companies set first_seen = least(coalesce(first_seen, %(d)s), %(d)s), "
                    "last_seen = greatest(coalesce(last_seen, %(d)s), %(d)s) where id = %(id)s",
                    {"d": seen, "id": company_id})

    def set_event_company(self, event_id: int, company_id: int | None) -> None:
        self._q("update public.events set company_id = %s where id = %s", (company_id, event_id))

    def rank_companies(self, profile, as_of: date | None = None) -> list[dict]:
        from pipeline import momentum
        as_of = as_of or date.today()
        companies = self.list_companies(profile.id)
        events = self._all(
            """select id, company_id, event_type, funding_amount, product, regulatory_status, date
               from public.events where industry_id = %s and company_id is not null and date between %s and %s""",
            (profile.id, as_of - timedelta(days=momentum.WINDOW_DAYS), as_of))
        return momentum.rank(profile, companies, [_plain(e) for e in events])

    # ── Job boards and postings ──────────────────────────────────────────────

    def upsert_board(self, provider: str, token: str, company: str | None) -> None:
        self._q("insert into public.job_boards (provider, token, company) values (%s, %s, %s) "
                "on conflict (provider, token) do update set company = coalesce(job_boards.company, excluded.company)",
                (provider, token, company))

    def list_boards(self) -> list[dict]:
        return self._all("select * from public.job_boards order by company")

    def mark_board_fetched(self, provider: str, token: str) -> None:
        self._q("update public.job_boards set last_fetched_at = now() where provider = %s and token = %s",
                (provider, token))

    @staticmethod
    def board_is_fresh(board: dict, max_age_hours: float) -> bool:
        fetched = board.get("last_fetched_at")
        if isinstance(fetched, str):
            fetched = datetime.fromisoformat(fetched)
        if fetched is None:
            return False
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        return fetched > datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

    def was_board_checked(self, normalized_name: str, max_age_days: int = 30) -> bool:
        return self._one("select 1 from public.job_board_checks where normalized_name = %s "
                         "and checked_at > now() - make_interval(days => %s)",
                         (normalized_name, max_age_days)) is not None

    def mark_board_checked(self, normalized_name: str) -> None:
        self._q("insert into public.job_board_checks (normalized_name, checked_at) values (%s, now()) "
                "on conflict (normalized_name) do update set checked_at = excluded.checked_at", (normalized_name,))

    def upsert_posting(self, posting: dict, industry_id: str | None) -> int:
        values = {k: posting.get(k) for k in POSTING_FIELDS}
        values["remote"] = _remote(values["remote"])
        with self._lock, self.conn.transaction():
            existing = self.conn.execute("select id from public.job_postings where url = %s",
                                         (posting["url"],)).fetchone()
            if existing:
                job_id = existing["id"]
                updates = {k: v for k, v in values.items() if v not in (None, "")}
                sets = "".join(f"{k} = %({k})s, " for k in updates)
                self.conn.execute(f"update public.job_postings set {sets}active = true, last_seen_at = now() "
                                  "where id = %(id)s", {**updates, "id": job_id})
            else:
                cols = ", ".join(POSTING_FIELDS)
                job_id = self.conn.execute(
                    f"insert into public.job_postings (url, {cols}) values (%(url)s, "
                    + ", ".join(f"%({k})s" for k in POSTING_FIELDS) + ") returning id",
                    {**values, "url": posting["url"]}).fetchone()["id"]
            if industry_id:
                self.conn.execute("insert into public.job_posting_industries (job_id, industry_id) "
                                  "values (%s, %s) on conflict do nothing", (job_id, industry_id))
        return job_id

    def deactivate_missing(self, provider: str, board_token: str, seen_urls: set[str]) -> int:
        cur = self._q("update public.job_postings set active = false where provider = %s and board_token = %s "
                      "and active and not (url = any(%s))", (provider, board_token, list(seen_urls)))
        return cur.rowcount

    def mark_posting_closed(self, job_id: int) -> None:
        self._q("update public.job_postings set active = false where id = %s", (job_id,))

    def refresh_posting(self, job_id: int, posting: dict | None) -> None:
        with self._lock, self.conn.transaction():
            if not posting:
                self.conn.execute("update public.job_postings set last_seen_at = now() where id = %s", (job_id,))
                return
            updates = {k: posting.get(k) for k in POSTING_FIELDS if posting.get(k) not in (None, "")}
            if "remote" in updates:
                updates["remote"] = _remote(updates["remote"])
            taken = self.conn.execute("select 1 from public.job_postings where url = %s and id <> %s",
                                      (posting["url"], job_id)).fetchone()
            if not taken:  # the posting moved (e.g. a renamed careers account)
                updates["url"] = posting["url"]
            sets = "".join(f"{k} = %({k})s, " for k in updates)
            self.conn.execute(f"update public.job_postings set {sets}last_seen_at = now() where id = %(id)s",
                              {**updates, "id": job_id})

    def postings_to_recheck(self, industry_id: str, older_than_hours: float,
                            providers: tuple[str, ...]) -> list[dict]:
        rows = self._all(
            """select p.* from public.job_postings p
               join public.job_posting_industries pi on pi.job_id = p.id and pi.industry_id = %s
               where p.active and p.provider = any(%s) and p.last_seen_at < now() - make_interval(hours => %s)""",
            (industry_id, list(providers), older_than_hours))
        return [{**r, "industry_ids": [industry_id]} for r in rows]

    # ── Worker bookkeeping ───────────────────────────────────────────────────

    def get_cache(self, key: str):
        row = self._one("select value from public.cache where key = %s and (expires_at is null or expires_at > now())",
                        (key,))
        return row["value"] if row else None

    def set_cache(self, key: str, value, expires_at: datetime | None = None) -> None:
        self._q("insert into public.cache (key, value, expires_at) values (%s, %s, %s) on conflict (key) do update "
                "set value = excluded.value, expires_at = excluded.expires_at, updated_at = now()",
                (key, Jsonb(value), expires_at))

