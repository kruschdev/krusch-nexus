"""
Krusch-Nexus — DBOS Swarm Queue Integration.

Provides read access to the external openclaw-db PostgreSQL container
(agent_execution_queue table) for surfacing swarm debate results and
human review items in the Nexus frontend.
"""

import os
import logging
import contextlib
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger(__name__)

DBOS_DATABASE_URL = os.getenv(
    "DBOS_DATABASE_URL",
    "postgres://openclaw:openclaw_password@10.0.0.85:5434/kruschdb"
)

# Global thread-safe connection pool for DBOS database. Lazy-initialized to prevent
# startup crashes if the database server or Tailscale network is temporarily down.
_dbos_pool = None


def get_dbos_pool():
    """Retrieve or initialize the thread-safe connection pool."""
    global _dbos_pool
    if _dbos_pool is None:
        try:
            # We configure a pool with min 2 and max 20 connections.
            _dbos_pool = ThreadedConnectionPool(
                minconn=2,
                maxconn=20,
                dsn=DBOS_DATABASE_URL
            )
            logger.info("[Swarm] Initialized ThreadedConnectionPool with max 20 connections.")
        except Exception as e:
            logger.error(f"[Swarm] Failed to initialize connection pool: {e}")
            raise e
    return _dbos_pool


@contextlib.contextmanager
def get_dbos_conn():
    """Context manager that yields a verified connection from the pool and ensures its return."""
    pool = get_dbos_pool()
    conn = None
    try:
        conn = pool.getconn()
        # Verify connection health, recreating it if closed
        if conn.closed:
            logger.warning("[Swarm] Pool yielded a closed connection, discarding and reconnecting.")
            try:
                pool.putconn(conn, close=True)
            except Exception:
                pass
            conn = psycopg2.connect(DBOS_DATABASE_URL)
        yield conn
    except Exception as e:
        logger.error(f"[Swarm] Database connection error in context manager: {e}")
        raise e
    finally:
        if conn is not None:
            try:
                # Always rollback any pending transactions to clean up recycled thread state
                conn.rollback()
            except Exception:
                pass
            try:
                pool.putconn(conn)
            except Exception as e:
                logger.error(f"[Swarm] Failed to return connection to pool: {e}")


def get_swarm_jobs(
    status: Optional[str] = None,
    job_type: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Fetch swarm jobs from the DBOS queue with optional filters."""
    conditions = []
    params = []

    if status:
        conditions.append("status = %s")
        params.append(status)
    if job_type:
        conditions.append("job_type = %s")
        params.append(job_type)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    query = f"""
        SELECT job_id, thread_id, job_type, payload, result, status,
               started_at_ms, picked_at_ms, completed_at_ms, created_at
        FROM agent_execution_queue
        {where_clause}
        ORDER BY started_at_ms DESC
        LIMIT %s
    """
    params.append(limit)

    try:
        with get_dbos_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query, params)
                rows = cur.fetchall()

        jobs = []
        for row in rows:
            job = dict(row)
            # Convert UUID to string for JSON serialization
            job["job_id"] = str(job["job_id"])
            job["thread_id"] = str(job["thread_id"])
            # Convert timestamps
            if job.get("created_at"):
                job["created_at"] = job["created_at"].isoformat()
            # Compute human-readable elapsed time
            if job.get("started_at_ms"):
                started = datetime.fromtimestamp(job["started_at_ms"] / 1000, tz=timezone.utc)
                job["started_at"] = started.isoformat()
            jobs.append(job)
        return jobs

    except Exception as e:
        logger.error(f"[Swarm] Failed to query DBOS queue: {e}")
        return []


def get_swarm_stats() -> dict:
    """Get aggregate stats for the swarm queue."""
    query = """
        SELECT 
            COUNT(*) as all_time_jobs,
            COUNT(*) FILTER (WHERE status IN ('pending', 'running')) as active_backlog,
            COUNT(*) FILTER (WHERE status = 'pending') as pending,
            COUNT(*) FILTER (WHERE status = 'running') as running,
            COUNT(*) FILTER (WHERE status = 'completed') as completed,
            COUNT(*) FILTER (WHERE job_type = 'human_review' AND status = 'pending') as pending_human_reviews
        FROM agent_execution_queue
    """
    try:
        with get_dbos_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query)
                row = cur.fetchone()
        return dict(row) if row else {}
    except Exception as e:
        logger.error(f"[Swarm] Failed to get stats: {e}")
        return {}


def get_jean_swarm_stats() -> dict:
    """Get swarm stats broken down by job_type for Jean SRE.

    Returns separate counts for Jean's intent jobs vs normal swarm
    debate activity, preventing Jean from conflating routine work
    with actionable problems.
    """
    query = """
        SELECT
            -- Jean's own intent jobs
            COUNT(*) FILTER (WHERE job_type = 'intent' AND status = 'pending') as intent_pending,
            COUNT(*) FILTER (WHERE job_type = 'intent' AND status = 'running') as intent_running,
            COUNT(*) FILTER (WHERE job_type = 'intent' AND status = 'completed') as intent_completed,
            COUNT(*) FILTER (WHERE job_type = 'intent' AND status = 'failed') as intent_failed,
            -- Normal swarm debate activity (NOT a problem)
            COUNT(*) FILTER (WHERE job_type IN ('debate_proposal', 'debate_rebuttal') AND status = 'running') as debate_running,
            COUNT(*) FILTER (WHERE job_type IN ('debate_proposal', 'debate_rebuttal') AND status = 'pending') as debate_pending,
            -- Other job types
            COUNT(*) FILTER (WHERE job_type NOT IN ('intent', 'debate_proposal', 'debate_rebuttal') AND status = 'pending') as other_pending,
            -- Overall
            COUNT(*) as total_jobs,
            COUNT(*) FILTER (WHERE status = 'completed') as total_completed,
            COUNT(*) FILTER (WHERE status = 'failed') as total_failed
        FROM agent_execution_queue
    """
    try:
        with get_dbos_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query)
                row = cur.fetchone()
        return dict(row) if row else {}
    except Exception as e:
        logger.error(f"[Swarm] Failed to get Jean stats: {e}")
        return {}


def get_human_reviews(limit: int = 20) -> list[dict]:
    """Fetch pending human review items — the synthesized prototype plans."""
    return get_swarm_jobs(status="pending", job_type="human_review", limit=limit)


def get_debate_thread(thread_id: str) -> list[dict]:
    """Fetch all jobs in a debate thread to show the full Idea → Critique → Synthesis chain."""
    query = """
        SELECT job_id, thread_id, job_type, payload, result, status,
               started_at_ms, picked_at_ms, completed_at_ms, created_at
        FROM agent_execution_queue
        WHERE thread_id = %s
        ORDER BY started_at_ms ASC
    """
    try:
        with get_dbos_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query, [thread_id])
                rows = cur.fetchall()

        jobs = []
        for row in rows:
            job = dict(row)
            job["job_id"] = str(job["job_id"])
            job["thread_id"] = str(job["thread_id"])
            if job.get("created_at"):
                job["created_at"] = job["created_at"].isoformat()
            if job.get("started_at_ms"):
                started = datetime.fromtimestamp(job["started_at_ms"] / 1000, tz=timezone.utc)
                job["started_at"] = started.isoformat()
            jobs.append(job)
        return jobs

    except Exception as e:
        logger.error(f"[Swarm] Failed to fetch thread {thread_id}: {e}")
        return []


def update_review_status(job_id: str, new_status: str) -> bool:
    """Mark a human review as approved/rejected/dismissed. If approved, dispatch for execution."""
    if new_status not in ("approved", "rejected", "dismissed"):
        return False
    try:
        import uuid
        import json
        with get_dbos_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Fetch the original review job
                cur.execute("SELECT thread_id, payload FROM agent_execution_queue WHERE job_id = %s", [job_id])
                row = cur.fetchone()
                if not row:
                    return False
                    
                # Update the review job
                cur.execute(
                    "UPDATE agent_execution_queue SET status = %s, completed_at_ms = %s WHERE job_id = %s",
                    [new_status, int(datetime.now(timezone.utc).timestamp() * 1000), job_id]
                )
                
                # If approved, spawn an execution job for the headless worker
                if new_status == "approved":
                    new_job_id = str(uuid.uuid4())
                    payload = row["payload"] or {}
                    instructions = payload.get("instructions", "No instructions provided.") if isinstance(payload, dict) else str(payload)
                    
                    exec_payload = {
                        "task": "execute_approved_plan",
                        "instructions": instructions,
                        "context": {
                            "approved_from_review": job_id
                        }
                    }
                    
                    cur.execute(
                        """
                        INSERT INTO agent_execution_queue (job_id, thread_id, job_type, payload, status, started_at_ms)
                        VALUES (%s, %s, %s, %s, 'pending', %s)
                        """,
                        [new_job_id, row["thread_id"], 'execution', json.dumps(exec_payload), int(datetime.now(timezone.utc).timestamp() * 1000)]
                    )
            conn.commit()
        return True
    except Exception as e:
        logger.error(f"[Swarm] Failed to update review {job_id}: {e}")
        return False


def execute_sandbox_test(command: str, cwd: str = "~") -> dict:
    """Submit a shell command to the DBOS queue to be executed on the kruschgame sandbox."""
    try:
        import uuid
        import time
        import json
        
        job_id = str(uuid.uuid4())
        thread_id = str(uuid.uuid4())
        
        payload = {
            "task": "execute_sandbox_test",
            "instructions": f"cd {cwd} && {command}",
            "context": {
                "dispatched_by": "hermes_mcp",
                "target_node": "kruschgame"
            }
        }
        
        with get_dbos_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agent_execution_queue (job_id, thread_id, job_type, payload, status, started_at_ms)
                    VALUES (%s, %s, %s, %s, 'pending', %s)
                    """,
                    [job_id, thread_id, 'intent', json.dumps(payload), int(datetime.now(timezone.utc).timestamp() * 1000)]
                )
            conn.commit()
        
        # Poll for completion up to 60 seconds.
        # We release the connection back to the pool during sleep to prevent starvation!
        start_time = time.time()
        while time.time() - start_time < 60:
            time.sleep(2)
            try:
                with get_dbos_conn() as conn:
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                        cur.execute("SELECT status, result FROM agent_execution_queue WHERE job_id = %s", [job_id])
                        row = cur.fetchone()
                
                if row:
                    if row['status'] == 'completed':
                        return {"status": "completed", "result": row['result']}
                    elif row['status'] == 'failed':
                        return {"status": "failed", "error": row['result']}
            except Exception as pe:
                logger.error(f"[Swarm] Failed to query polling status for job {job_id}: {pe}")
                
        return {"error": "Job timed out after 60 seconds."}
        
    except Exception as e:
        logger.error(f"[Swarm] Failed to execute sandbox test: {e}")
        return {"error": str(e)}


def get_agent_memories(
    category: Optional[str] = None,
    project: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Fetch episodic memories from ide_agent_memory table with optional filters."""
    conditions = []
    params = []

    if category:
        conditions.append("category = %s")
        params.append(category)
    if project:
        conditions.append("project = %s")
        params.append(project)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    query = f"""
        SELECT id, category, content, project, tags, created_at
        FROM ide_agent_memory
        {where_clause}
        ORDER BY created_at DESC
        LIMIT %s
    """
    params.append(limit)

    try:
        with get_dbos_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query, params)
                rows = cur.fetchall()

        memories = []
        for row in rows:
            mem = dict(row)
            mem["id"] = str(mem["id"])
            if mem.get("created_at"):
                mem["created_at"] = mem["created_at"].isoformat()
            memories.append(mem)
        return memories

    except Exception as e:
        logger.error(f"[Swarm] Failed to query ide_agent_memory: {e}")
        return []
