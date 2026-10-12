"""Daily research briefs."""
from datetime import date

from fastapi import APIRouter, Depends

from api import focus
from api.auth import CurrentUser, current_user
from api.db import as_user
from api.deps import get_conn
from api.errors import not_found

router = APIRouter(tags=["briefs"])


@router.get("/briefs")
def get_brief(industry_id: str, date: date | None = None,
              user: CurrentUser = Depends(current_user), conn=Depends(get_conn)):
    with as_user(conn, user.id):
        row = conn.execute(
            """select b.id, b.industry_id, i.name as industry_name, b.brief_date, b.headline, b.content_md,
                      b.created_at, b.run_id
               from public.briefs b join public.industries i on i.id = b.industry_id
               where b.industry_id = %(industry)s and (%(date)s::date is null or b.brief_date = %(date)s)
               order by b.brief_date desc limit 1""",
            {"industry": industry_id, "date": date}).fetchone()
        if row is None:
            raise not_found("No brief yet for this industry and date.")
        # The brief is shared; the user's focus only picks out matching events from its run.
        mine = focus.user_focuses(conn, user.id).get(industry_id)
        focus_events = []
        if mine and row["run_id"]:
            focus_events = conn.execute(
                """select id, date, company, event_type, summary, source_url, source_type from public.events
                   where run_id = %s and (coalesce(summary, '') || ' ' || coalesce(company, '') || ' ' ||
                         coalesce(product, '') || ' ' || replace(segment, '_', ' ')) ~* %s
                   order by date desc nulls last limit 10""", (row["run_id"], mine["pattern"])).fetchall()
    return {**row, "focus": mine["label"] if mine else None, "focus_events": focus_events}


@router.get("/briefs/dates")
def brief_dates(industry_id: str, user: CurrentUser = Depends(current_user), conn=Depends(get_conn)):
    with as_user(conn, user.id):
        rows = conn.execute(
            "select brief_date from public.briefs where industry_id = %s order by brief_date desc limit 365",
            (industry_id,)).fetchall()
    return {"dates": [r["brief_date"] for r in rows]}
