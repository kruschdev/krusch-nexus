"""
scripts/run_migrations.py
=========================
Robust migration runner and readiness probe for PostgreSQL in CI.
Polls DATABASE_URL until TCP connectivity is established, verifies connection,
initializes the pgvector extension with autocommit, and executes Alembic upgrade head.
"""

import os
import sys
import time
import traceback
import psycopg2
from alembic.config import Config
from alembic import command


def run_migrations() -> None:
    db_url = os.environ.get("DATABASE_URL", "")
    print(f"[run_migrations] Target DATABASE_URL: {db_url}")

    if db_url.startswith("postgres"):
        print("[run_migrations] Polling PostgreSQL container on 127.0.0.1:5432...")
        start_time = time.time()
        ready = False
        last_err = None
        for attempt in range(1, 31):
            try:
                conn = psycopg2.connect(db_url, connect_timeout=3)
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute("SELECT 1;")
                    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                conn.close()
                elapsed = time.time() - start_time
                print(f"[run_migrations] PostgreSQL 16 & pgvector ready in {elapsed:.1f}s (attempt {attempt}).")
                ready = True
                break
            except Exception as exc:
                last_err = exc
                print(f"[run_migrations] Attempt {attempt}/30 failed: {exc}. Retrying in 2s...")
                time.sleep(2)

        if not ready:
            err_msg = f"PostgreSQL failed to become ready: {last_err}"
            print(f"::error title=Postgres Readiness Error::{err_msg}", file=sys.stderr)
            sys.exit(1)

    print("[run_migrations] Executing Alembic upgrade head...")
    try:
        config = Config("alembic.ini")
        command.upgrade(config, "head")
        print("[run_migrations] Alembic migrations / bootstrap completed successfully.")
    except Exception as exc:
        tb = traceback.format_exc()
        print(f"[run_migrations] Traceback:\n{tb}", file=sys.stderr)
        single_line_err = f"{type(exc).__name__}: {str(exc)}".replace("\n", " ")
        print(f"::error title=Alembic Migration Error::{single_line_err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    run_migrations()
