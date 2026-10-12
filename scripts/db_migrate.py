"""Apply supabase/migrations/*.sql in order, then supabase/seed.sql.

Applied migrations are recorded in supabase_migrations.schema_migrations, the same
table the Supabase CLI uses, so the CLI can take over later without re-running them.

Usage:  python scripts/db_migrate.py            # apply pending migrations + seed
        python scripts/db_migrate.py --status   # list applied / pending
"""
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import DATABASE_URL  # noqa: E402

MIGRATIONS_DIR = ROOT / "supabase" / "migrations"
SEED_FILE = ROOT / "supabase" / "seed.sql"

TRACKING_SQL = """
    create schema if not exists supabase_migrations;
    create table if not exists supabase_migrations.schema_migrations (
        version text primary key,
        statements text[],
        name text
    );
"""


def _migration_files() -> list[tuple[str, str, Path]]:
    files = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version, _, name = path.stem.partition("_")
        files.append((version, name, path))
    return files


def _applied(conn) -> set[str]:
    return {r[0] for r in conn.execute("select version from supabase_migrations.schema_migrations")}


def migrate(database_url: str = DATABASE_URL, status_only: bool = False) -> list[str]:
    if not database_url:
        raise SystemExit("DATABASE_URL is not set (see .env)")
    applied_now = []
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(TRACKING_SQL)
        done = _applied(conn)
        for version, name, path in _migration_files():
            if version in done:
                print(f"  applied  {path.name}")
                continue
            if status_only:
                print(f"  pending  {path.name}")
                continue
            sql = path.read_text(encoding="utf-8")
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    "insert into supabase_migrations.schema_migrations (version, statements, name) "
                    "values (%s, %s, %s)", (version, [sql], name))
            print(f"  applied  {path.name}  (new)")
            applied_now.append(path.name)
        if not status_only:
            with conn.transaction():
                conn.execute(SEED_FILE.read_text(encoding="utf-8"))
            print(f"  seeded   {SEED_FILE.name}")
    return applied_now


if __name__ == "__main__":
    migrate(status_only="--status" in sys.argv)
