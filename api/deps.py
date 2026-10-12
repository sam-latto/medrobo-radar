"""Shared request dependencies."""
from fastapi import Request


def get_conn(request: Request):
    """One database connection for the whole request."""
    with request.app.state.db.connection() as conn:
        yield conn
