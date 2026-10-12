"""Which database the pipeline and job collector write to.

The local app uses SQLite (the default). The hosted worker runs the same code against
Postgres by wrapping its work in `use_store(PgStore(conn))`. Code reads the current
store with `store()`; threads started inside that work must copy the context
(contextvars.copy_context) to see it.
"""
import contextvars
from contextlib import contextmanager

_current = contextvars.ContextVar("data_store", default=None)


class SqliteStore:
    """Forwards to the original SQLite functions (looked up at call time, so tests
    that monkeypatch those modules still take effect)."""

    def __getattr__(self, name: str):
        from database import companies, db, jobs_db
        if name == "rank_companies":
            from pipeline.momentum import rank_companies
            return rank_companies
        for module in (db, companies, jobs_db):
            if hasattr(module, name):
                return getattr(module, name)
        raise AttributeError(name)


SQLITE = SqliteStore()


def store():
    return _current.get() or SQLITE


@contextmanager
def use_store(data_store):
    token = _current.set(data_store)
    try:
        yield data_store
    finally:
        _current.reset(token)
