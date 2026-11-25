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


def create_task(conn, user_id: UUID, kind: str) -> UUID:
    with as_service(conn):
        return conn.execute(
            "insert into public.tasks (user_id, kind) values (%s, %s) returning id",
            (user_id, kind)).fetchone()["id"]


def _update(conn, task_id: UUID, **fields) -> None:
    sets = ", ".join(f"{k} = %({k})s" for k in fields)
    params = {k: Jsonb(v) if k in ("progress", "result") else v for k, v in fields.items()}
    with as_service(conn):
        conn.execute(f"update public.tasks set {sets}, updated_at = now() where id = %(id)s",
                     {**params, "id": task_id})


def run_task(db, task_id: UUID, user_id: UUID, usage_id: int | None,
             work: Callable[[object, Callable[[str], None]], dict]) -> None:
    """Run `work(conn, progress)`; store its result, or the failure, on the task."""
    collector = UsageCollector()
    token = usage_sink.set(collector.add)
    with db.connection() as conn:
        def progress(message: str) -> None:
            _update(conn, task_id, status="running", progress={"message": message})

        try:
            progress("Starting…")
            result = work(conn, progress)
            _update(conn, task_id, status="complete", result=result,
                    progress={"message": "Done"})
        except Exception as e:
            logger.exception(f"Task {task_id} failed")
            _update(conn, task_id, status="failed",
                    error="This didn't finish because of an error on our side. It wasn't counted "
                          "against your limit; please try again.")
            if usage_id is not None:
                limits.release(conn, usage_id)
        finally:
            usage_sink.reset(token)
            try:
                collector.flush(conn, user_id)
            except Exception:
                logger.exception(f"Could not record usage for task {task_id}")
