import sqlite3
from contextlib import contextmanager
from config import DB_PATH


def init_db():
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id TEXT PRIMARY KEY,
                run_date DATE NOT NULL,
                triggered_by TEXT NOT NULL,
                status TEXT NOT NULL,
                briefing_text TEXT,
                completed_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                company TEXT,
                product TEXT,
                event_type TEXT NOT NULL,
                sub_segment TEXT NOT NULL,
                date DATE,
                summary TEXT NOT NULL,
                source_url TEXT NOT NULL,
                funding_amount INTEGER,
                fda_status TEXT,
                is_notable BOOLEAN NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (run_id) REFERENCES pipeline_runs(id)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_events_source_url
                ON events(source_url);

            CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id);
            CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type);
            CREATE INDEX IF NOT EXISTS idx_events_sub_segment ON events(sub_segment);
        """)


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


def url_exists(url: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM events WHERE source_url = ?", (url,)
        ).fetchone()
        return row is not None


def insert_run(run_id: str, run_date: str, triggered_by: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO pipeline_runs (id, run_date, triggered_by, status) VALUES (?, ?, ?, 'running')",
            (run_id, run_date, triggered_by),
        )


def update_run(run_id: str, status: str, briefing_text: str = None) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE pipeline_runs SET status = ?, briefing_text = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, briefing_text, run_id),
        )


def insert_event(run_id: str, event: dict) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO events
               (run_id, company, product, event_type, sub_segment, date, summary,
                source_url, funding_amount, fda_status, is_notable)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                event.get("company"),
                event.get("product"),
                event["event_type"],
                event["sub_segment"],
                event.get("date"),
                event["summary"],
                event["source_url"],
                event.get("funding_amount"),
                event.get("fda_status"),
                int(event.get("is_notable", False)),
            ),
        )


def get_all_events(
    sub_segment: str = None,
    event_type: str = None,
    date_from: str = None,
    date_to: str = None,
    company: str = None,
) -> list[dict]:
    clauses = []
    params = []

    if sub_segment:
        clauses.append("sub_segment = ?")
        params.append(sub_segment)
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

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM events {where} ORDER BY created_at DESC",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_notable_events(run_id: str = None) -> list[dict]:
    if run_id:
        clause = "WHERE is_notable = 1 AND run_id = ?"
        params = [run_id]
    else:
        clause = "WHERE is_notable = 1"
        params = []
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM events {clause} ORDER BY created_at DESC LIMIT 50",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_latest_run() -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM pipeline_runs ORDER BY run_date DESC, completed_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def get_all_runs() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM pipeline_runs ORDER BY run_date DESC"
        ).fetchall()
    return [dict(r) for r in rows]
