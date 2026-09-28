"""
scripts/wait_for_postgres.py
============================
Readiness probe and extension initializer for PostgreSQL service containers in CI.
Polls DATABASE_URL until TCP connectivity is established, verifies connection,
and initializes the pgvector extension with autocommit before running Alembic migrations.
"""

import os
import sys
import time
import psycopg2


def wait_for_postgres(timeout_seconds: int = 60, interval_seconds: float = 2.0) -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url or not db_url.startswith("postgres"):
        print("[wait_for_postgres] DATABASE_URL is not PostgreSQL. Skipping probe.")
        return

    print(f"[wait_for_postgres] Polling PostgreSQL readiness (timeout={timeout_seconds}s)...")
    start_time = time.time()
    attempt = 1

    while time.time() - start_time < timeout_seconds:
        try:
            conn = psycopg2.connect(db_url, connect_timeout=3)
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            conn.close()
            elapsed = time.time() - start_time
            print(f"[wait_for_postgres] PostgreSQL 16 & pgvector extension ready after {elapsed:.1f}s (attempt {attempt}).")
            return
        except Exception as exc:
            print(f"[wait_for_postgres] Attempt {attempt} failed: {exc}. Retrying in {interval_seconds}s...")
            time.sleep(interval_seconds)
            attempt += 1

    print(f"[wait_for_postgres] ERROR: PostgreSQL failed to become ready within {timeout_seconds}s.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    wait_for_postgres()
