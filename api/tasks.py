"""Background tasks for slow, metered work (job search, resume tailoring).

The API creates a task row and returns its id at once; the work runs after the
response on its own connection, writing progress the front end polls. Failures release
the usage reservation, and every Claude call's cost is recorded for the user.
"""
import logging
from typing import Callable
from uuid import UUID

from psycopg.types.json import Jsonb

from api import limits
from api.db import as_service
from api.usage import UsageCollector
from pipeline.llm import usage_sink

logger = logging.getLogger(__name__)


STALE_TASK_MINUTES = 30
REFUNDED = "This didn't finish because of an error on our side. It wasn't counted against your limit; please try again."
CHARGED = ("This didn't finish because of an error on our side, after part of the work had already run, "
           "so it still counts toward your limit. Please try again tomorrow, or contact us if it keeps happening.")


def start_metered_task(conn, user_id: UUID, kind: str, action: str, ref: str) -> tuple[UUID, int | None, bool]:
    """Reserve one use of `action` and create the task, in one transaction.

    If the same work (`ref`) is already queued or running for this user, e.g. a double
    click, returns that task instead and charges nothing. Returns (task_id, usage_id, is_new)."""
    with as_service(conn):
        conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"{user_id}:task:{ref}",))
        existing = conn.execute(
            "select id from public.tasks where user_id = %s and ref = %s and status in ('queued', 'running')",
            (user_id, ref)).fetchone()
        if existing:
            return existing["id"], None, False
        usage_id = limits.reserve(conn, user_id, action)
        task_id = conn.execute(
            "insert into public.tasks (user_id, kind, usage_id, ref) values (%s, %s, %s, %s) returning id",
            (user_id, kind, usage_id, ref)).fetchone()["id"]
    return task_id, usage_id, True


def _update(conn, task_id: UUID, **fields) -> None:
    sets = ", ".join(f"{k} = %({k})s" for k in fields)
    params = {k: Jsonb(v) if k in ("progress", "result") else v for k, v in fields.items()}
    with as_service(conn):
        conn.execute(f"update public.tasks set {sets}, updated_at = now() where id = %(id)s",
                     {**params, "id": task_id})


def _fail(db, conn, task_id: UUID, usage_id: int | None, refund: bool) -> None:
    """Mark a task failed (refunding its reservation if asked), on a fresh connection if
    the task's own connection is the thing that broke."""
    def attempt(c):
        if refund and usage_id is not None:
            limits.release(c, usage_id)
        _update(c, task_id, status="failed", error=REFUNDED if refund else CHARGED)
    try:
        attempt(conn)
    except Exception:
        logger.exception(f"Could not mark task {task_id} failed on its connection; retrying on a new one")
        with db.connection() as fresh:
            attempt(fresh)


def run_task(db, task_id: UUID, user_id: UUID, usage_id: int | None,
             work: Callable[[object, Callable[[str], None]], dict]) -> None:
    """Run `work(conn, progress)`; store its result, or the failure, on the task.

    A failure refunds the user's reservation only if no paid call was made; otherwise
    the work cost money and it counts (this also stops repeat-until-refunded abuse)."""
    collector = UsageCollector()
    token = usage_sink.set(collector.add)
    try:
        with db.connection() as conn:
            def progress(message: str) -> None:
                _update(conn, task_id, status="running", progress={"message": message})

            try:
                progress("Starting…")
                result = work(conn, progress)
                _update(conn, task_id, status="complete", result=result, progress={"message": "Done"})
            except Exception:
                logger.exception(f"Task {task_id} failed")
                _fail(db, conn, task_id, usage_id, refund=not collector.records)
            finally:
                try:
                    collector.flush(conn, user_id)
                except Exception:
                    logger.exception(f"Could not record usage for task {task_id}")
    finally:
        usage_sink.reset(token)


def sweep_stale_tasks(conn, older_than_minutes: int = STALE_TASK_MINUTES) -> int:
    """Fail tasks stuck in queued/running (e.g. the server restarted mid-task) and refund
    their reservations. Run at API startup and by the daily worker."""
    with as_service(conn):
        stale = conn.execute(
            """update public.tasks set status = 'failed', error = %s, updated_at = now()
               where status in ('queued', 'running') and updated_at < now() - make_interval(mins => %s)
               returning usage_id""", (REFUNDED, older_than_minutes)).fetchall()
        ids = [r["usage_id"] for r in stale if r["usage_id"] is not None]
        if ids:
            conn.execute("delete from public.usage_events where id = any(%s)", (ids,))
    if stale:
        logger.warning(f"Failed {len(stale)} stuck task(s) and refunded {len(ids)} reservation(s)")
    return len(stale)
