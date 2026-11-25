"""Companies: momentum-ranked lists, company profiles, and the user's watchlist."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends

from api.auth import CurrentUser, current_user
from api.db import as_user
from api.deps import get_conn
from api.errors import ApiError, not_found
from api.routes.events import source_name
from pipeline import momentum
from profiles.loader import parse_profile

router = APIRouter(tags=["companies"])


def ranked_companies(conn, industry_id: str, as_of: date | None = None) -> list[dict]:
    """Momentum ranking for one industry from Postgres (caller sets the transaction)."""
    industry = conn.execute("select id, profile_yaml from public.industries where id = %s",
                            (industry_id,)).fetchone()
    if industry is None:
        raise not_found("Unknown industry.")
    companies = conn.execute(
        """select c.id, c.name, c.normalized_name, c.description, c.website
           from public.companies c join public.company_industries ci on ci.company_id = c.id
           where ci.industry_id = %s""", (industry_id,)).fetchall()
    if not industry["profile_yaml"]:  # not tracked yet: list companies without scores
        return [{"company_id": c["id"], "company": c["name"], "watchlist": False, "score": 0, "events_90d": 0,
                 "funding_90d": 0, "regulatory_90d": 0, "last_event": None, "rank": i}
                for i, c in enumerate(sorted(companies, key=lambda c: c["name"].lower()), 1)]
    as_of = as_of or date.today()
    events = conn.execute(
        """select id, company_id, event_type, funding_amount, product, regulatory_status, date
           from public.events
           where industry_id = %s and company_id is not null and date between %s and %s""",
        (industry_id, as_of - timedelta(days=momentum.WINDOW_DAYS), as_of)).fetchall()
    for e in events:  # momentum's dedup keys expect ISO date strings
        e["date"] = e["date"].isoformat() if e["date"] else None
    return momentum.rank(parse_profile(industry["profile_yaml"]), companies, events)


@router.get("/companies")
def list_companies(industry_id: str, q: str | None = None, watching: bool = False,
                   user: CurrentUser = Depends(current_user), conn=Depends(get_conn)):
    with as_user(conn, user.id):
        ranking = ranked_companies(conn, industry_id)
        info = {c["id"]: c for c in conn.execute(
            """select c.id, c.description, c.website from public.companies c
               join public.company_industries ci on ci.company_id = c.id where ci.industry_id = %s""",
            (industry_id,))}
        watched = {r["company_id"] for r in conn.execute(
            "select company_id from public.watchlist where user_id = %s", (user.id,))}
    items = []
    needle = (q or "").strip().lower()
    for r in ranking:
        if needle and needle not in r["company"].lower():
            continue
        if watching and r["company_id"] not in watched:
            continue
        extra = info.get(r["company_id"], {})
        key_company = r.pop("watchlist")  # on the industry's list of key companies
        items.append({**r, "key_company": key_company, "watching": r["company_id"] in watched,
                      "description": extra.get("description"), "website": extra.get("website")})
    return {"items": items, "total": len(items)}


@router.get("/companies/{company_id}")
def get_company(company_id: int, user: CurrentUser = Depends(current_user), conn=Depends(get_conn)):
    with as_user(conn, user.id):
        company = conn.execute(
            "select id, name, description, website, first_seen, last_seen from public.companies where id = %s",
            (company_id,)).fetchone()
        if company is None:
            raise not_found("Unknown company.")
        industries = conn.execute(
            """select i.id, i.name from public.company_industries ci join public.industries i
               on i.id = ci.industry_id where ci.company_id = %s order by i.sort_order""",
            (company_id,)).fetchall()
        events = conn.execute(
            """select id, date, industry_id, event_type, segment, product, summary, source_url, source_type,
                      funding_amount, regulatory_status
               from public.events where company_id = %s order by date desc nulls last, id desc limit 100""",
            (company_id,)).fetchall()
        # Companies sharing an industry, ranked by their recent events in those industries
        similar = conn.execute(
            """with mine as (select industry_id from public.company_industries where company_id = %(id)s),
                    peers as (select distinct ci.company_id from public.company_industries ci
                              join mine using (industry_id) where ci.company_id <> %(id)s)
               select c.id, c.name, c.description,
                      (select count(*) from public.events e
                       where e.company_id = c.id and e.industry_id in (select industry_id from mine)
                         and e.date between current_date - %(days)s and current_date) as events_90d
               from peers join public.companies c on c.id = peers.company_id
               order by events_90d desc, c.name limit 5""",
            {"id": company_id, "days": momentum.WINDOW_DAYS}).fetchall()
        hiring = conn.execute(
            """select id, title, location, url, posted_at from public.job_postings
               where active and lower(company) = lower(%s) order by first_seen_at desc limit 10""",
            (company["name"],)).fetchall()
        watching = conn.execute("select 1 from public.watchlist where user_id = %s and company_id = %s",
                                (user.id, company_id)).fetchone() is not None
    for e in events:
        e["source_name"] = source_name(e["source_type"], e["source_url"])
    by_source = {
        "filings": [e for e in events if e["source_type"] in ("sec_form_d", "openfda")],
        "trials": [e for e in events if e["source_type"] == "clinicaltrials"],
        "research": [e for e in events if e["source_type"] in ("pubmed", "arxiv")],
    }
    return {**company, "industries": industries, "watching": watching, "events": events, **by_source,
            "hiring": {"open_roles": len(hiring), "postings": hiring}, "similar": similar}


@router.put("/watchlist/{company_id}", status_code=204)
def watch(company_id: int, user: CurrentUser = Depends(current_user), conn=Depends(get_conn)):
    with as_user(conn, user.id):
        if conn.execute("select 1 from public.companies where id = %s", (company_id,)).fetchone() is None:
            raise not_found("Unknown company.")
        count = conn.execute("select count(*) as n from public.watchlist where user_id = %s",
                             (user.id,)).fetchone()["n"]
        if count >= 500:
            raise ApiError(422, "watchlist_full", "Your watchlist is full (500 companies).")
        conn.execute("insert into public.watchlist (user_id, company_id) values (%s, %s) on conflict do nothing",
                     (user.id, company_id))


@router.delete("/watchlist/{company_id}", status_code=204)
def unwatch(company_id: int, user: CurrentUser = Depends(current_user), conn=Depends(get_conn)):
    with as_user(conn, user.id):
        conn.execute("delete from public.watchlist where user_id = %s and company_id = %s", (user.id, company_id))
