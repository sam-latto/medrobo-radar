"""Industry catalog, the user's followed industries, and requests for new ones."""
import re
from typing import Annotated

import psycopg

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, StringConstraints

from api.auth import CurrentUser, current_user
from api.db import as_user
from api.deps import get_conn
from api.errors import ApiError

router = APIRouter(tags=["industries"])

MAX_SUBSCRIPTIONS = 25


def catalog_tree(rows: list[dict]) -> list[dict]:
    """Sectors with their industries, plus stand-alone industries, in catalog order."""
    by_parent: dict[str | None, list[dict]] = {}
    for r in rows:
        by_parent.setdefault(r["parent_id"], []).append(r)

    def node(r: dict) -> dict:
        out = {"id": r["id"], "name": r["name"], "kind": r["kind"],
               "followable": r["kind"] == "industry",
               "tracking_active": r["status"] == "active" and r["has_profile"]}
        if r["kind"] == "sector":
            out["industries"] = [node(c) for c in by_parent.get(r["id"], [])]
        return out

    return [node(r) for r in by_parent.get(None, [])]


@router.get("/industries")
def list_industries(user: CurrentUser = Depends(current_user), conn=Depends(get_conn)):
    with as_user(conn, user.id):
        rows = conn.execute(
            """select id, parent_id, name, kind, status, profile_yaml is not null as has_profile
               from public.industries where status <> 'retired' order by sort_order, name""").fetchall()
    return {"sectors": catalog_tree(rows)}


class Subscription(BaseModel):
    industry_id: str
    focus: str | None = Field(default=None, max_length=120)


class SubscriptionsUpdate(BaseModel):
    industries: list[Subscription] = Field(max_length=MAX_SUBSCRIPTIONS)


@router.put("/subscriptions")
def replace_subscriptions(body: SubscriptionsUpdate, user: CurrentUser = Depends(current_user),
                          conn=Depends(get_conn)):
    wanted = {s.industry_id: s.focus for s in body.industries}
    with as_user(conn, user.id):
        valid = {r["id"] for r in conn.execute(
            "select id from public.industries where id = any(%s) and kind = 'industry' and status <> 'retired'",
            (list(wanted),))}
        unknown = sorted(set(wanted) - valid)
        if unknown:
            raise ApiError(422, "unknown_industry", f"Unknown industry: {', '.join(unknown)}")
        conn.execute("delete from public.subscriptions where user_id = %s", (user.id,))
        with conn.cursor() as cur:
            cur.executemany(
                "insert into public.subscriptions (user_id, industry_id, focus) values (%s, %s, %s)",
                [(user.id, i, f) for i, f in wanted.items()])
        conn.execute("update public.profiles set onboarded_at = coalesce(onboarded_at, now()) where id = %s",
                     (user.id,))
        rows = conn.execute(
            """select s.industry_id, i.name, s.focus from public.subscriptions s
               join public.industries i on i.id = s.industry_id
               where s.user_id = %s order by i.sort_order""", (user.id,)).fetchall()
    return {"subscriptions": rows}


class IndustryRequest(BaseModel):
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in {"and", "the"}}


def best_match(label: str, industries: list[dict]) -> dict | None:
    """The catalog industry sharing the most words with `label` (simple first pass;
    a reviewer or Claude can refine matches later)."""
    words = _words(label)
    scored = [(len(words & _words(i["name"])), i) for i in industries]
    scored = [s for s in scored if s[0] > 0]
    return max(scored, key=lambda s: s[0])[1] if scored else None


@router.post("/industries/requests", status_code=201)
def request_industry(body: IndustryRequest, user: CurrentUser = Depends(current_user), conn=Depends(get_conn)):
    label = body.label
    name = label[0].upper() + label[1:]
    with as_user(conn, user.id):
        industries = conn.execute(
            "select id, name from public.industries where kind = 'industry' and status <> 'retired'").fetchall()
        match = best_match(label, industries)
        status = "focus" if match else "pending_review"
        conn.execute(
            "insert into public.custom_industry_requests (user_id, label, matched_industry_id, status) "
            "values (%s, %s, %s, %s)", (user.id, name, match["id"] if match else None, status))
        if match:
            following = conn.execute("select count(*) as n from public.subscriptions where user_id = %s",
                                     (user.id,)).fetchone()["n"]
            already = conn.execute("select focus from public.subscriptions where user_id = %s and industry_id = %s",
                                   (user.id, match["id"])).fetchone()
            if already is None and following >= MAX_SUBSCRIPTIONS:
                raise ApiError(422, "too_many_industries",
                               f"You can follow up to {MAX_SUBSCRIPTIONS} industries. Remove one first.")
            # Keep a focus the user already set on that industry
            conn.execute(
                """insert into public.subscriptions (user_id, industry_id, focus) values (%s, %s, %s)
                   on conflict (user_id, industry_id) do update
                   set focus = coalesce(public.subscriptions.focus, excluded.focus)""",
                (user.id, match["id"], name))
    if match:
        message = (f'"{name}" added as a focus within {match["name"]}. Your brief for '
                   f'{match["name"]} covers it.')
    else:
        message = f'"{name}" doesn\'t fit an existing area yet, so we\'ll review it and set up tracking.'
    return {"label": name, "status": status, "matched_industry_id": match["id"] if match else None,
            "message": message}
