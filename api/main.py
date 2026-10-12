"""FastAPI app. Locally: uvicorn api.main:app --reload  (docs at http://localhost:8000/docs)"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import errors
from api.routes import briefs, companies, events, industries, jobs, me

API_PREFIX = "/api/v1"
WEB_ORIGINS = [o.strip() for o in os.getenv("WEB_ORIGINS", "http://localhost:3000").split(",") if o.strip()]

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


def create_app(db=None, verifier=None, admin=None) -> FastAPI:
    """Build the app. Tests pass their own db / verifier / admin; production builds real ones."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from api.stores import PgKeywordCache
        from jobs import functions
        if db is None:
            from api.db import PooledDatabase
            app.state.db = PooledDatabase()
        previous_cache = (functions._cache["get"], functions._cache["set"])
        cache = PgKeywordCache(app.state.db)
        functions.set_keyword_cache(cache.get, cache.set)
        try:  # tasks left running by a restart: fail them and refund the user
            from api.tasks import sweep_stale_tasks
            with app.state.db.connection() as conn:
                sweep_stale_tasks(conn)
        except Exception:
            logging.getLogger(__name__).exception("Stale task sweep failed")
        yield
        functions.set_keyword_cache(*previous_cache)
        if db is None:
            app.state.db.close()

    app = FastAPI(title="Industry Radar API", version="1.0", lifespan=lifespan,
                  docs_url=f"{API_PREFIX}/docs", openapi_url=f"{API_PREFIX}/openapi.json")
    if db is not None:
        app.state.db = db
    if verifier is None:
        from api.auth import TokenVerifier
        verifier = TokenVerifier()
    if admin is None:
        from api.supabase_admin import SupabaseAdmin
        admin = SupabaseAdmin()
    app.state.verifier, app.state.admin = verifier, admin

    app.add_middleware(CORSMiddleware, allow_origins=WEB_ORIGINS, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["Authorization", "Content-Type"])
    errors.install(app)

    @app.get("/health")
    def health():
        return {"ok": True}

    for module in (me, industries, briefs, events, companies, jobs):
        app.include_router(module.router, prefix=API_PREFIX)
    return app


app = create_app()
