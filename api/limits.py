"""Plan limits for metered actions (e.g. 1 job search per day, 30 tailorings per month).

A metered action reserves a usage_events row before it starts; if the work fails the
reservation is released, so only successful runs count. The check and the insert run
under a per-user, per-action advisory lock, so double clicks can't exceed the limit.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from api.db import as_service
from api.errors import ApiError

FIND_JOBS = "find_jobs"
TAILOR_RESUME = "tailor_resume"

ACTION_LABELS = {FIND_JOBS: ("job search", "job searches"),
                 TAILOR_RESUME: ("resume tailoring", "resume tailorings")}


def period_bounds(per: str, now: datetime) -> tuple[datetime, datetime]:
    """Start and end (exclusive) of the UTC day or calendar month containing `now`."""
    now = now.astimezone(timezone.utc)
    if per == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    if per == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
        return start, end
    raise ValueError(f"Unknown limit period: {per}")


def limit_message(action: str, limit: dict, resets: datetime) -> str:
    one, many = ACTION_LABELS.get(action, (action, action))
    period = "per day" if limit["per"] == "day" else "per month"
    count = f"1 {one}" if limit["max"] == 1 else f"{limit['max']} {many}"
    when = "tomorrow" if limit["per"] == "day" else f"on {resets:%b} {resets.day}"
    return f"You've reached your limit of {count} {period}. It resets {when}."


def plan_limits(conn, user_id: UUID) -> dict:
    row = conn.execute(
        """select pl.limits from public.profiles p
           join public.communities c on c.id = p.community_id
           join public.plans pl on pl.id = coalesce(p.plan_id, c.plan_id)
           where p.id = %s""", (user_id,)).fetchone()
    return (row or {}).get("limits") or {}


def _used(conn, user_id: UUID, action: str, start: datetime) -> int:
    return conn.execute(
        "select count(*) as n from public.usage_events where user_id = %s and action = %s and created_at >= %s",
        (user_id, action, start)).fetchone()["n"]


SPEND_PAUSED_KEY = "spend_paused"  # set by the worker when the monthly budget is used up


def reserve(conn, user_id: UUID, action: str, now: datetime | None = None) -> int:
    """Count one use of `action` for the user, or raise 429 if the plan's limit is reached
    (503 while the app's monthly budget is used up)."""
    now = now or datetime.now(timezone.utc)
    with as_service(conn):
        paused = conn.execute(
            "select 1 from public.cache where key = %s and (expires_at is null or expires_at > now())",
            (SPEND_PAUSED_KEY,)).fetchone()
        if paused:
            raise ApiError(503, "paused", "Job search and resume tailoring are paused until the start of next "
                                          "month. Your briefs and saved results are still available.")
        conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"{user_id}:{action}",))
        limit = plan_limits(conn, user_id).get(action)
        if limit:
            start, end = period_bounds(limit["per"], now)
            if _used(conn, user_id, action, start) >= limit["max"]:
                raise ApiError(429, "limit_reached", limit_message(action, limit, end),
                               retry_after=end.isoformat())
        return conn.execute(
            "insert into public.usage_events (user_id, action) values (%s, %s) returning id",
            (user_id, action)).fetchone()["id"]


def release(conn, usage_id: int) -> None:
    with as_service(conn):
        conn.execute("delete from public.usage_events where id = %s", (usage_id,))


def summary(conn, user_id: UUID, now: datetime | None = None) -> dict:
    """{action: {used, max, per, remaining, resets_at}} for each limited action."""
    now = now or datetime.now(timezone.utc)
    out = {}
    with as_service(conn):
        for action, limit in plan_limits(conn, user_id).items():
            start, end = period_bounds(limit["per"], now)
            used = _used(conn, user_id, action, start)
            out[action] = {"used": used, "max": limit["max"], "per": limit["per"],
                           "remaining": max(0, limit["max"] - used), "resets_at": end.isoformat()}
    return out
