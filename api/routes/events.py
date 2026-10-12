"""Event tracker: filterable, paged events with their sources, plus CSV export."""
import csv
import io
from datetime import date
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from api import focus
from api.auth import CurrentUser, current_user
from api.db import as_user
from api.deps import get_conn

router = APIRouter(tags=["events"])

SOURCE_NAMES = {
    "openfda": "FDA database",
    "sec_form_d": "SEC Form D",
    "pubmed": "PubMed",
    "arxiv": "arXiv",
    "clinicaltrials": "ClinicalTrials.gov",
}
CSV_LIMIT = 5000


def source_name(source_type: str, url: str) -> str:
    if source_type in SOURCE_NAMES:
        return SOURCE_NAMES[source_type]
    host = urlsplit(url or "").hostname or ""
    return host.removeprefix("www.") or "Source"


def event_filters(user_id, industry_id: str | None, event_type: str | None, segment: str | None,
                  date_from: date | None, date_to: date | None, q: str | None,
                  focus_match: str | None = None) -> tuple[str, dict]:
    clauses = []
    params: dict = {"user": user_id}
    if focus_match:
        clauses.append(focus_match)
    if industry_id:
        clauses.append("e.industry_id = %(industry)s")
        params["industry"] = industry_id
    else:  # default: everything the user follows
        clauses.append("e.industry_id in (select industry_id from public.subscriptions where user_id = %(user)s)")
    if event_type:
        clauses.append("e.event_type = %(type)s")
        params["type"] = event_type
    if segment:
        clauses.append("e.segment = %(segment)s")
        params["segment"] = segment
    if date_from:
        clauses.append("e.date >= %(from)s")
        params["from"] = date_from
    if date_to:
        clauses.append("e.date <= %(to)s")
        params["to"] = date_to
    if q and q.strip():
        clauses.append("(e.summary ilike %(q)s or e.company ilike %(q)s or e.product ilike %(q)s)")
        params["q"] = f"%{q.strip()}%"
    return " and ".join(clauses), params


def select_sql(focus_match: str) -> str:
    return f"""
    select e.id, e.date, e.industry_id, e.company, e.company_id, e.product, e.event_type, e.segment,
           e.summary, e.source_url, e.source_type, e.funding_amount, e.regulatory_status,
           (w.company_id is not null) as watching, {focus_match} as matches_focus
    from public.events e
    left join public.watchlist w on w.company_id = e.company_id and w.user_id = %(user)s
"""


def _with_source(rows: list[dict]) -> list[dict]:
    for r in rows:
        r["source_name"] = source_name(r["source_type"], r["source_url"])
    return rows


@router.get("/events")
def list_events(industry_id: str | None = None, type: str | None = None, segment: str | None = None,
                date_from: date | None = Query(default=None, alias="from"),
                date_to: date | None = Query(default=None, alias="to"),
                q: str | None = None, focus_only: bool = False, page: int = Query(default=1, ge=1),
                page_size: int = Query(default=25, ge=1, le=100),
                user: CurrentUser = Depends(current_user), conn=Depends(get_conn)):
    with as_user(conn, user.id):
        focus_params: dict = {}
        focus_match = focus.focus_sql(focus.user_focuses(conn, user.id), focus_params)
        where, params = event_filters(user.id, industry_id, type, segment, date_from, date_to, q,
                                      focus_match if focus_only else None)
        params.update(focus_params)
        total = conn.execute(f"select count(*) as n from public.events e where {where}", params).fetchone()["n"]
        rows = conn.execute(
            f"{select_sql(focus_match)} where {where} order by e.date desc nulls last, e.id desc "
            "limit %(limit)s offset %(offset)s",
            {**params, "limit": page_size, "offset": (page - 1) * page_size}).fetchall()
    return {"items": _with_source(rows), "total": total, "page": page, "page_size": page_size}


@router.get("/events.csv")
def export_events(industry_id: str | None = None, type: str | None = None, segment: str | None = None,
                  date_from: date | None = Query(default=None, alias="from"),
                  date_to: date | None = Query(default=None, alias="to"),
                  q: str | None = None, user: CurrentUser = Depends(current_user), conn=Depends(get_conn)):
    where, params = event_filters(user.id, industry_id, type, segment, date_from, date_to, q)
    with as_user(conn, user.id):
        rows = conn.execute(f"{select_sql('false')} where {where} order by e.date desc nulls last, e.id desc "
                            f"limit {CSV_LIMIT}", params).fetchall()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["date", "industry", "company", "event_type", "segment", "summary", "funding_usd",
                     "regulatory_status", "source", "source_url"])
    for r in _with_source(rows):
        writer.writerow([r["date"] or "", r["industry_id"], r["company"] or "", r["event_type"], r["segment"],
                         r["summary"], r["funding_amount"] or "", r["regulatory_status"] or "",
                         r["source_name"], r["source_url"]])
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="events.csv"'})
