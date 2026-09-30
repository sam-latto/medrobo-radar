import json
import logging
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from config import DB_PATH

logger = logging.getLogger(__name__)

LEGACY_INDUSTRY_ID = "healthcare_robotics"

SCHEMA = """
    CREATE TABLE IF NOT EXISTS pipeline_runs (
        id TEXT PRIMARY KEY,
        industry_id TEXT,
        run_date DATE NOT NULL,
        triggered_by TEXT NOT NULL,
        status TEXT NOT NULL,
        briefing_text TEXT,
        source_counts TEXT,
        completed_at TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        industry_id TEXT NOT NULL,
        company TEXT,
        company_id INTEGER,
        product TEXT,
        event_type TEXT NOT NULL,
        segment TEXT NOT NULL,
        date DATE,
        summary TEXT NOT NULL,
        source_url TEXT NOT NULL,
        source_type TEXT NOT NULL DEFAULT 'tavily',
        funding_amount INTEGER,
        regulatory_status TEXT,
        is_notable BOOLEAN NOT NULL DEFAULT 0,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (run_id) REFERENCES pipeline_runs(id)
    );

    CREATE UNIQUE INDEX IF NOT EXISTS idx_events_industry_url
        ON events(industry_id, source_url);

    CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id);
    CREATE INDEX IF NOT EXISTS idx_events_industry ON events(industry_id);
    CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type);
    CREATE INDEX IF NOT EXISTS idx_events_segment ON events(segment);
    CREATE INDEX IF NOT EXISTS idx_events_date ON events(date);
    CREATE INDEX IF NOT EXISTS idx_runs_industry ON pipeline_runs(industry_id, run_date);
"""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _needs_v1_migration() -> bool:
    if not Path(DB_PATH).exists():
        return False
    conn = sqlite3.connect(DB_PATH)
    try:
        cols = _columns(conn, "events")
        return bool(cols) and "industry_id" not in cols
    finally:
        conn.close()


def _backup_db(suffix: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = Path(f"{DB_PATH}.bak-{suffix}-{stamp}")
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(backup_path)
    try:
        src.backup(dst)  # consistent copy, includes any WAL contents
    finally:
        dst.close()
        src.close()
    return backup_path


def _migrate_v1(conn: sqlite3.Connection) -> None:
    """One-time v1 -> v2 migration: single-industry schema to multi-industry.

    - events: add industry_id/source_type/company_id, rename sub_segment -> segment
      and fda_status -> regulatory_status, event_type 'fda' -> 'regulatory',
      unique key becomes (industry_id, source_url)
    - pipeline_runs: add industry_id
    Existing rows are assigned to the healthcare_robotics profile.
    """
    conn.executescript(f"""
        CREATE TABLE events_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            industry_id TEXT NOT NULL,
            company TEXT,
            company_id INTEGER,
            product TEXT,
            event_type TEXT NOT NULL,
            segment TEXT NOT NULL,
            date DATE,
            summary TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT 'tavily',
            funding_amount INTEGER,
            regulatory_status TEXT,
            is_notable BOOLEAN NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES pipeline_runs(id)
        );

        INSERT INTO events_v2
            (id, run_id, industry_id, company, product, event_type, segment, date, summary,
             source_url, source_type, funding_amount, regulatory_status, is_notable, created_at)
        SELECT id, run_id, '{LEGACY_INDUSTRY_ID}', company, product,
               CASE event_type WHEN 'fda' THEN 'regulatory' ELSE event_type END,
               sub_segment, date, summary, source_url, 'tavily', funding_amount, fda_status,
               is_notable, created_at
        FROM events;

        DROP TABLE events;
        ALTER TABLE events_v2 RENAME TO events;
    """)
    if "industry_id" not in _columns(conn, "pipeline_runs"):
        conn.execute("ALTER TABLE pipeline_runs ADD COLUMN industry_id TEXT")
    conn.execute(
        "UPDATE pipeline_runs SET industry_id = ? WHERE industry_id IS NULL",
        (LEGACY_INDUSTRY_ID,),
    )


def init_db():
    if _needs_v1_migration():
        backup = _backup_db("v1")
        logger.info(f"Migrating v1 database to multi-industry schema (backup: {backup})")
        with get_connection() as conn:
            _migrate_v1(conn)
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        if "source_counts" not in _columns(conn, "pipeline_runs"):
            conn.execute("ALTER TABLE pipeline_runs ADD COLUMN source_counts TEXT")
        # Placeholder dates like '<UNKNOWN>' break date filters; store them as NULL
        conn.execute("UPDATE events SET date = NULL WHERE date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'")


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _where(clauses: list[str]) -> str:
    return ("WHERE " + " AND ".join(clauses)) if clauses else ""


def url_exists(url: str, industry_id: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM events WHERE industry_id = ? AND source_url = ?", (industry_id, url)
        ).fetchone()
        return row is not None


def insert_run(run_id: str, run_date: str, triggered_by: str, industry_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO pipeline_runs (id, industry_id, run_date, triggered_by, status)
               VALUES (?, ?, ?, ?, 'running')""",
            (run_id, industry_id, run_date, triggered_by),
        )


def update_run(run_id: str, status: str, briefing_text: str = None, source_counts: dict = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """UPDATE pipeline_runs
               SET status = ?, briefing_text = ?, source_counts = COALESCE(?, source_counts),
                   completed_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (status, briefing_text, json.dumps(source_counts) if source_counts is not None else None, run_id),
        )


def insert_event(run_id: str, event: dict) -> int | None:
    """Insert an event; returns its id, or None if (industry_id, source_url) already exists."""
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO events
               (run_id, industry_id, company, company_id, product, event_type, segment, date,
                summary, source_url, source_type, funding_amount, regulatory_status, is_notable)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                event["industry_id"],
                event.get("company"),
                event.get("company_id"),
                event.get("product"),
                event["event_type"],
                event["segment"],
                event.get("date"),
                event["summary"],
                event["source_url"],
                event.get("source_type") or "tavily",
                event.get("funding_amount"),
                event.get("regulatory_status"),
                int(event.get("is_notable", False)),
            ),
        )
        return cur.lastrowid if cur.rowcount else None


def get_all_events(
    industry_id: str = None,
    segment: str = None,
    event_type: str = None,
    date_from: str = None,
    date_to: str = None,
    company: str = None,
    source_type: str = None,
) -> list[dict]:
    clauses = []
    params = []

    if industry_id:
        clauses.append("industry_id = ?")
        params.append(industry_id)
    if segment:
        clauses.append("segment = ?")
        params.append(segment)
    if event_type:
        clauses.append("event_type = ?")
        params.append(event_type)
    if date_from:
        clauses.append("date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("date <= ?")
        params.append(date_to)
    if company:
        clauses.append("company LIKE ?")
        params.append(f"%{company}%")
    if source_type:
        clauses.append("source_type = ?")
        params.append(source_type)

    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM events {_where(clauses)} ORDER BY created_at DESC",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def count_events(industry_id: str = None) -> int:
    clauses, params = ([], [])
    if industry_id:
        clauses.append("industry_id = ?")
        params.append(industry_id)
    with get_connection() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM events {_where(clauses)}", params).fetchone()[0]


def get_notable_events(industry_id: str = None) -> list[dict]:
    clauses = ["is_notable = 1", "date >= date('now', '-7 days')"]
    params = []
    if industry_id:
        clauses.append("industry_id = ?")
        params.append(industry_id)
    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT * FROM events {_where(clauses)}
                ORDER BY date ASC, created_at ASC
                LIMIT 50""",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_latest_run(industry_id: str = None) -> dict | None:
    clauses, params = ([], [])
    if industry_id:
        clauses.append("industry_id = ?")
        params.append(industry_id)
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT * FROM pipeline_runs {_where(clauses)} ORDER BY run_date DESC, completed_at DESC LIMIT 1",
            params,
        ).fetchone()
    return dict(row) if row else None


def get_all_runs(industry_id: str = None) -> list[dict]:
    clauses, params = ([], [])
    if industry_id:
        clauses.append("industry_id = ?")
        params.append(industry_id)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM pipeline_runs {_where(clauses)} ORDER BY run_date DESC, completed_at DESC",
            params,
        ).fetchall()
    return [dict(r) for r in rows]
