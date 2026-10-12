"""Account: profile, settings, data export and deletion."""
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from api import limits
from api.auth import CurrentUser, current_user
from api.db import as_service, as_user
from api.deps import get_conn
from api.errors import ApiError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["account"])


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    email_brief_opt_in: bool | None = None


def _profile(conn, user: CurrentUser) -> dict:
    with as_user(conn, user.id):
        profile = conn.execute(
            """select p.id, p.email, p.display_name, p.email_brief_opt_in, p.onboarded_at, p.created_at,
                      c.slug as community, c.name as community_name
               from public.profiles p join public.communities c on c.id = p.community_id
               where p.id = %s""", (user.id,)).fetchone()
        subs = conn.execute(
            """select s.industry_id, i.name, s.focus from public.subscriptions s
               join public.industries i on i.id = s.industry_id
               where s.user_id = %s order by i.sort_order""", (user.id,)).fetchall()
    return {**profile, "subscriptions": subs, "usage": limits.summary(conn, user.id)}


@router.get("/me")
def get_me(user: CurrentUser = Depends(current_user), conn=Depends(get_conn)):
    return _profile(conn, user)


@router.patch("/me")
def update_me(body: ProfileUpdate, user: CurrentUser = Depends(current_user), conn=Depends(get_conn)):
    changes = body.model_dump(exclude_unset=True)
    if "email_brief_opt_in" in changes and changes["email_brief_opt_in"] is None:
        raise ApiError(422, "invalid_request", "email_brief_opt_in must be true or false.")
    if changes:
        sets = ", ".join(f"{k} = %({k})s" for k in changes)
        with as_user(conn, user.id):
            conn.execute(f"update public.profiles set {sets} where id = %(id)s", {**changes, "id": user.id})
    return _profile(conn, user)


EXPORT_TABLES = ["subscriptions", "custom_industry_requests", "watchlist", "resumes", "job_preferences",
                 "job_matches", "resume_advice", "usage_events"]


@router.get("/me/export")
def export_me(user: CurrentUser = Depends(current_user), conn=Depends(get_conn)):
    data = {"profile": _profile(conn, user)}
    with as_user(conn, user.id):
        for table in EXPORT_TABLES:
            data[table] = conn.execute(f"select * from public.{table} where user_id = %s",
                                       (user.id,)).fetchall()
    return JSONResponse(jsonable_encoder(data), headers={
        "Content-Disposition": 'attachment; filename="industry-radar-export.json"'})


@router.delete("/me", status_code=204)
def delete_me(request: Request, user: CurrentUser = Depends(current_user), conn=Depends(get_conn)):
    admin = request.app.state.admin
    try:
        admin.delete_files(admin.list_user_files(str(user.id)))
    except Exception:
        logger.exception("Could not delete resume files for a deleted account")
        raise ApiError(502, "delete_failed", "We couldn't delete your files. Nothing was removed; please try again.")
    # Deleting the auth user cascades to the profile and every private table.
    admin.delete_user(str(user.id))
    with as_service(conn):
        conn.execute("delete from public.profiles where id = %s", (user.id,))
