"""Postgres access for the API.

Every request borrows one connection (autocommit) from a pool. Work is done inside
explicit transactions in one of two modes:

- as_user(conn, user_id): runs as the `authenticated` role with the user's id in the
  JWT claims, so row-level security applies exactly as for a direct Supabase query.
- as_service(conn): runs as the connection's own role (bypasses RLS). Only for shared
  writes and server bookkeeping, and every query must filter by user id itself.
"""
import json
from contextlib import contextmanager
from uuid import UUID

from psycopg.rows import dict_row

from config import DATABASE_URL


class PooledDatabase:
    """Production connection source. psycopg_pool is imported lazily so the rest of the
    API (and its tests) can load without it."""

    def __init__(self, url: str = DATABASE_URL, min_size: int = 1, max_size: int = 5):
        from psycopg_pool import ConnectionPool
        self.pool = ConnectionPool(
            url, min_size=min_size, max_size=max_size, open=True,
            kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None})

    @contextmanager
    def connection(self):
        with self.pool.connection() as conn:
            yield conn

    def close(self):
        self.pool.close()


@contextmanager
def as_user(conn, user_id: UUID | str):
    with conn.transaction():
        conn.execute("set local role authenticated")
        conn.execute("select set_config('request.jwt.claims', %s, true)",
                     (json.dumps({"sub": str(user_id), "role": "authenticated"}),))
        yield conn
        # Reached only on success. Undo the role switch explicitly in case this was a
        # nested transaction (savepoint), whose SET LOCAL would otherwise outlive it.
        conn.execute("reset role")
        conn.execute("select set_config('request.jwt.claims', '', true)")


@contextmanager
def as_service(conn):
    with conn.transaction():
        yield conn
