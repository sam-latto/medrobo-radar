"""Company records: one row per real company, linked from events.company_id."""
import json

from database.db import get_connection

def _row(r) -> dict:
    d = dict(r)
    d["industry_ids"] = json.loads(d.get("industry_ids") or "[]")
    return d


def get_company(company_id: int) -> dict | None:
    with get_connection() as conn:
        r = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    return _row(r) if r else None


def get_company_by_normalized(normalized_name: str) -> dict | None:
    with get_connection() as conn:
        r = conn.execute(
            "SELECT * FROM companies WHERE normalized_name = ?", (normalized_name,)
        ).fetchone()
    return _row(r) if r else None


def list_companies(industry_id: str | None = None) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM companies ORDER BY name").fetchall()
    companies = [_row(r) for r in rows]
    if industry_id:
        companies = [c for c in companies if industry_id in c["industry_ids"]]
    return companies


def insert_company(name: str, normalized_name: str, industry_id: str | None = None) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO companies (name, normalized_name, industry_ids) VALUES (?, ?, ?)",
            (name, normalized_name, json.dumps([industry_id] if industry_id else [])),
        )
        if cur.rowcount:
            return cur.lastrowid
        return conn.execute(
            "SELECT id FROM companies WHERE normalized_name = ?", (normalized_name,)
        ).fetchone()[0]


def touch_company(company_id: int, industry_id: str | None, seen_date: str | None) -> None:
    """Add industry_id to the company's industries and widen first_seen/last_seen."""
    with get_connection() as conn:
        r = conn.execute(
            "SELECT industry_ids, first_seen, last_seen FROM companies WHERE id = ?", (company_id,)
        ).fetchone()
        if r is None:
            return
        industries = json.loads(r["industry_ids"] or "[]")
        if industry_id and industry_id not in industries:
            industries.append(industry_id)
        first, last = r["first_seen"], r["last_seen"]
        if seen_date:
            first = min(first, seen_date) if first else seen_date
            last = max(last, seen_date) if last else seen_date
        conn.execute(
            "UPDATE companies SET industry_ids = ?, first_seen = ?, last_seen = ? WHERE id = ?",
            (json.dumps(industries), first, last, company_id),
        )


def set_event_company(event_id: int, company_id: int | None) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE events SET company_id = ? WHERE id = ?", (company_id, event_id))


def get_unlinked_events() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, industry_id, company, date FROM events
               WHERE company_id IS NULL AND company IS NOT NULL AND TRIM(company) != ''"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_company_events(company_id: int, industry_id: str | None = None,
                       date_from: str | None = None) -> list[dict]:
    clauses, params = ["company_id = ?"], [company_id]
    if industry_id:
        clauses.append("industry_id = ?")
        params.append(industry_id)
    if date_from:
        clauses.append("date >= ?")
        params.append(date_from)
    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT * FROM events WHERE {' AND '.join(clauses)}
                ORDER BY date IS NULL, date DESC, id DESC""",
            params,
        ).fetchall()
    return [dict(r) for r in rows]
