"""Storage for the Jobs workflow: boards, postings, resumes, match scores and advice."""
import json
from datetime import datetime, timedelta

from database.db import get_connection

JOBS_SCHEMA = """
    CREATE TABLE IF NOT EXISTS job_boards (
        provider TEXT NOT NULL,            -- greenhouse | lever | ashby | workday
        token TEXT NOT NULL,               -- board slug (workday: "host|tenant|site")
        company TEXT,
        last_fetched_at TIMESTAMP,
        PRIMARY KEY (provider, token)
    );

    -- Companies whose job boards we have already looked for (so discovery runs once)
    CREATE TABLE IF NOT EXISTS job_board_checks (
        normalized_name TEXT PRIMARY KEY,
        checked_at TIMESTAMP NOT NULL
    );

    CREATE TABLE IF NOT EXISTS job_postings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL UNIQUE,
        provider TEXT NOT NULL,            -- greenhouse | lever | ashby | workday | web
        board_token TEXT,
        company TEXT,
        title TEXT NOT NULL,
        location TEXT,
        remote INTEGER,                    -- 1 remote, 0 on-site/hybrid, NULL unknown
        department TEXT,
        employment_type TEXT,
        compensation TEXT,
        description TEXT,
        posted_at TEXT,
        industry_ids TEXT NOT NULL DEFAULT '[]',
        active INTEGER NOT NULL DEFAULT 1,
        first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_job_postings_board ON job_postings(provider, board_token);

    CREATE TABLE IF NOT EXISTS resumes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT,
        text TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS job_matches (
        job_id INTEGER NOT NULL,
        resume_id INTEGER NOT NULL,
        score INTEGER NOT NULL,
        summary TEXT,
        strengths TEXT,                    -- JSON list
        gaps TEXT,                         -- JSON list
        level_fit TEXT,
        industry_match INTEGER,            -- 0 when the employer isn't in the target industries
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (job_id, resume_id)
    );

    -- Generated advice: kind 'overall' (key = functions/industry) or 'job' (key = job id)
    CREATE TABLE IF NOT EXISTS resume_advice (
        resume_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        key TEXT NOT NULL,
        content TEXT NOT NULL,             -- JSON
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (resume_id, kind, key)
    );

    CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL                -- JSON
    );
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── Settings ──────────────────────────────────────────────────────────────────

def get_setting(key: str, default=None):
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(key: str, value) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


# ── Boards ────────────────────────────────────────────────────────────────────

def upsert_board(provider: str, token: str, company: str | None) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO job_boards (provider, token, company) VALUES (?, ?, ?) "
            "ON CONFLICT(provider, token) DO UPDATE SET company = COALESCE(job_boards.company, excluded.company)",
            (provider, token, company),
        )


def list_boards() -> list[dict]:
    with get_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM job_boards ORDER BY company").fetchall()]


def mark_board_fetched(provider: str, token: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE job_boards SET last_fetched_at = ? WHERE provider = ? AND token = ?",
            (_now(), provider, token),
        )


def board_is_fresh(board: dict, max_age_hours: float) -> bool:
    fetched = board.get("last_fetched_at")
    return bool(fetched) and datetime.fromisoformat(fetched) > datetime.now() - timedelta(hours=max_age_hours)


def was_board_checked(normalized_name: str, max_age_days: int = 30) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT checked_at FROM job_board_checks WHERE normalized_name = ?", (normalized_name,)
        ).fetchone()
    return bool(row) and datetime.fromisoformat(row["checked_at"]) > datetime.now() - timedelta(days=max_age_days)


def mark_board_checked(normalized_name: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO job_board_checks (normalized_name, checked_at) VALUES (?, ?) "
            "ON CONFLICT(normalized_name) DO UPDATE SET checked_at = excluded.checked_at",
            (normalized_name, _now()),
        )


# ── Postings ──────────────────────────────────────────────────────────────────

_POSTING_FIELDS = ("provider", "board_token", "company", "title", "location", "remote", "department",
                   "employment_type", "compensation", "description", "posted_at")


def upsert_posting(posting: dict, industry_id: str | None) -> int:
    """Insert or refresh a posting (keyed by URL); returns its id."""
    with get_connection() as conn:
        row = conn.execute("SELECT id, industry_ids FROM job_postings WHERE url = ?", (posting["url"],)).fetchone()
        if row:
            industries = json.loads(row["industry_ids"])
            if industry_id and industry_id not in industries:
                industries.append(industry_id)
            updates = {k: posting.get(k) for k in _POSTING_FIELDS if posting.get(k) not in (None, "")}
            sets = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(
                f"UPDATE job_postings SET {sets}{', ' if sets else ''}industry_ids = ?, active = 1, last_seen_at = ? WHERE id = ?",
                [*updates.values(), json.dumps(industries), _now(), row["id"]],
            )
            return row["id"]
        cur = conn.execute(
            f"""INSERT INTO job_postings (url, {', '.join(_POSTING_FIELDS)}, industry_ids)
                VALUES (?, {', '.join('?' for _ in _POSTING_FIELDS)}, ?)""",
            [posting["url"], *[posting.get(k) for k in _POSTING_FIELDS],
             json.dumps([industry_id] if industry_id else [])],
        )
        return cur.lastrowid


def deactivate_missing(provider: str, board_token: str, seen_urls: set[str]) -> int:
    """Postings no longer on a board are closed."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, url FROM job_postings WHERE provider = ? AND board_token = ? AND active = 1",
            (provider, board_token),
        ).fetchall()
        closed = [r["id"] for r in rows if r["url"] not in seen_urls]
        conn.executemany("UPDATE job_postings SET active = 0 WHERE id = ?", [(i,) for i in closed])
    return len(closed)


def get_posting(job_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM job_postings WHERE id = ?", (job_id,)).fetchone()
    return _posting_row(row) if row else None


def update_posting_company(job_id: int, company: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE job_postings SET company = ? WHERE id = ?", (company, job_id))


def list_active_postings(industry_ids: list[str] | None = None) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM job_postings WHERE active = 1").fetchall()
    postings = [_posting_row(r) for r in rows]
    if industry_ids:
        wanted = set(industry_ids)
        postings = [p for p in postings if wanted & set(p["industry_ids"])]
    return postings


def _posting_row(row) -> dict:
    d = dict(row)
    d["industry_ids"] = json.loads(d.get("industry_ids") or "[]")
    return d


# ── Resumes ───────────────────────────────────────────────────────────────────

def save_resume(filename: str | None, text: str) -> int:
    with get_connection() as conn:
        return conn.execute(
            "INSERT INTO resumes (filename, text) VALUES (?, ?)", (filename, text)
        ).lastrowid


def get_latest_resume() -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM resumes ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


# ── Matches and advice ────────────────────────────────────────────────────────

def save_match(job_id: int, resume_id: int, score: int, summary: str, strengths: list, gaps: list,
               level_fit: str | None, industry_match: bool | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO job_matches (job_id, resume_id, score, summary, strengths, gaps, level_fit, industry_match)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(job_id, resume_id) DO UPDATE SET score = excluded.score, summary = excluded.summary,
                 strengths = excluded.strengths, gaps = excluded.gaps, level_fit = excluded.level_fit,
                 industry_match = excluded.industry_match, created_at = CURRENT_TIMESTAMP""",
            (job_id, resume_id, score, summary, json.dumps(strengths), json.dumps(gaps), level_fit,
             None if industry_match is None else int(industry_match)),
        )


def get_matches(resume_id: int) -> dict[int, dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM job_matches WHERE resume_id = ?", (resume_id,)).fetchall()
    out = {}
    for r in rows:
        d = dict(r)
        d["strengths"] = json.loads(d["strengths"] or "[]")
        d["gaps"] = json.loads(d["gaps"] or "[]")
        out[d["job_id"]] = d
    return out


def get_advice(resume_id: int, kind: str, key: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT content FROM resume_advice WHERE resume_id = ? AND kind = ? AND key = ?",
            (resume_id, kind, key),
        ).fetchone()
    return json.loads(row["content"]) if row else None


def save_advice(resume_id: int, kind: str, key: str, content: dict) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO resume_advice (resume_id, kind, key, content) VALUES (?, ?, ?, ?)
               ON CONFLICT(resume_id, kind, key) DO UPDATE SET content = excluded.content,
                 created_at = CURRENT_TIMESTAMP""",
            (resume_id, kind, key, json.dumps(content)),
        )
