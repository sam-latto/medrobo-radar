"""Read-only, parameterized queries used by the Ask agent's tools.

Nothing here accepts SQL from the caller: filters become bound parameters and
`group_by` is looked up in a fixed whitelist.
"""
from database.db import get_connection

MAX_LIMIT = 100

EVENT_COLUMNS = (
    "id, industry_id, company, company_id, product, event_type, segment, date, summary, "
    "source_url, source_type, funding_amount, regulatory_status, is_notable"
)

GROUP_BY_SQL = {
    "company": "COALESCE(company, 'Unknown')",
    "segment": "segment",
    "event_type": "event_type",
    "industry": "industry_id",
    "source_type": "source_type",
    "month": "substr(date, 1, 7)",
    "quarter": "substr(date, 1, 4) || '-Q' || ((CAST(substr(date, 6, 2) AS INTEGER) + 2) / 3)",
    "year": "substr(date, 1, 4)",
}


def _event_filters(
    industry_id: str | None = None,
    segment: str | None = None,
    event_type: str | None = None,
    company: str | None = None,
    company_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_funding: int | None = None,
    source_type: str | None = None,
    notable_only: bool = False,
    text: str | None = None,
    undated_only: bool = False,
) -> tuple[list[str], list]:
    clauses: list[str] = []
    params: list = []

    def add(clause: str, *values):
        clauses.append(clause)
        params.extend(values)

    if industry_id:
        add("industry_id = ?", industry_id)
    if segment:
        add("segment = ?", segment)
    if event_type:
        add("event_type = ?", event_type)
    if company:
        add("company LIKE ?", f"%{company}%")
    if company_id is not None:
        add("company_id = ?", company_id)
    if date_from:
        add("date >= ?", date_from)
    if date_to:
        add("date <= ?", date_to)
    if min_funding is not None:
        add("funding_amount >= ?", min_funding)
    if source_type:
        add("source_type = ?", source_type)
    if notable_only:
        add("is_notable = 1")
    if undated_only:
        add("date IS NULL")
    if text:
        like = f"%{text}%"
        add("(summary LIKE ? OR company LIKE ? OR product LIKE ? OR regulatory_status LIKE ?)",
            like, like, like, like)
    return clauses, params


def _where(clauses: list[str]) -> str:
    return ("WHERE " + " AND ".join(clauses)) if clauses else ""


def search_events(limit: int = 25, **filters) -> list[dict]:
    clauses, params = _event_filters(**filters)
    limit = max(1, min(int(limit), MAX_LIMIT))
    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT {EVENT_COLUMNS} FROM events {_where(clauses)}
                ORDER BY date IS NULL, date DESC, id DESC LIMIT ?""",
            [*params, limit],
        ).fetchall()
    return [dict(r) for r in rows]


def count_matching_events(**filters) -> int:
    clauses, params = _event_filters(**filters)
    with get_connection() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM events {_where(clauses)}", params).fetchone()[0]


def aggregate_events(group_by: str, limit: int = 50, **filters) -> list[dict]:
    if group_by not in GROUP_BY_SQL:
        raise ValueError(f"group_by must be one of {sorted(GROUP_BY_SQL)}")
    expr = GROUP_BY_SQL[group_by]
    clauses, params = _event_filters(**filters)
    if group_by in ("month", "quarter", "year"):
        clauses.append("date IS NOT NULL")
    order = "grp" if group_by in ("month", "quarter", "year") else "event_count DESC, total_funding DESC"
    limit = max(1, min(int(limit), MAX_LIMIT))
    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT {expr} AS grp,
                       COUNT(*) AS event_count,
                       COALESCE(SUM(funding_amount), 0) AS total_funding,
                       SUM(CASE WHEN funding_amount IS NOT NULL THEN 1 ELSE 0 END) AS events_with_funding,
                       GROUP_CONCAT(id) AS event_ids
                FROM events {_where(clauses)}
                GROUP BY grp ORDER BY {order} LIMIT ?""",
            [*params, limit],
        ).fetchall()
    out = []
    for r in rows:
        ids = [int(i) for i in (r["event_ids"] or "").split(",") if i][:20]
        out.append({
            group_by: r["grp"],
            "event_count": r["event_count"],
            "total_funding_usd": r["total_funding"],
            "events_with_funding": r["events_with_funding"],
            "sample_event_ids": ids,
        })
    return out


def get_briefings(industry_id: str | None = None, date_from: str | None = None,
                  date_to: str | None = None, limit: int = 5) -> list[dict]:
    clauses = ["status = 'complete'", "briefing_text IS NOT NULL"]
    params: list = []
    if industry_id:
        clauses.append("industry_id = ?")
        params.append(industry_id)
    if date_from:
        clauses.append("run_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("run_date <= ?")
        params.append(date_to)
    limit = max(1, min(int(limit), 20))
    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT id AS run_id, industry_id, run_date, completed_at, briefing_text
                FROM pipeline_runs {_where(clauses)}
                ORDER BY run_date DESC, completed_at DESC LIMIT ?""",
            [*params, limit],
        ).fetchall()
    return [dict(r) for r in rows]


def get_events_by_ids(ids: list[int]) -> list[dict]:
    if not ids:
        return []
    ids = [int(i) for i in ids][:200]
    placeholders = ",".join("?" for _ in ids)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {EVENT_COLUMNS} FROM events WHERE id IN ({placeholders})", ids
        ).fetchall()
    return [dict(r) for r in rows]
