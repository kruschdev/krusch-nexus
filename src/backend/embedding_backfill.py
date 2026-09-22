#!/usr/bin/env python3
"""
Embedding Backfill for kruschdb
Re-embeds blobs using bge-large (1024 dims) via local Ollama GPUs.
Prioritizes summary text when available for better semantic search quality.
Runs in parallel with the Gemini summary backfill.
"""

import os
import time
import itertools
import httpx
import concurrent.futures
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Configuration
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://openclaw:openclaw_password@10.0.0.85:5434/kruschdb")
EMBED_MODEL = "bge-large"
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "400"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "48"))

# All 3 GPUs for embedding (bge-large is tiny, fits alongside 14b)
OLLAMA_NODES = [
    {"host": "http://10.0.0.144:11434", "label": "kruschdev GPU0"},
    {"host": "http://10.0.0.144:11435", "label": "kruschdev GPU1"},
    {"host": "http://10.0.0.85:11434",  "label": "kruschserv 2080Ti"},
    {"host": "http://10.0.0.85:11434",  "label": "kruschserv 2080Ti"},  # 2× weight — 2080Ti is twice as fast
]

_node_cycle = itertools.cycle(OLLAMA_NODES)


def get_node():
    return next(_node_cycle)


engine = create_engine(DATABASE_URL, pool_size=MAX_WORKERS + 2, max_overflow=5)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def embed_blob(blob_id, summary, file_name, file_path, content):
    """Generate embedding for a single blob. Uses summary if available, else raw content."""
    try:
        # Prefer summary for embedding (much better semantic signal)
        if summary and len(summary.strip()) > 5 and summary != '[SKIP]':
            embed_text = f"{file_name or file_path or 'unknown'}: {summary}"
        else:
            # Fall back to raw content
            if isinstance(content, (bytes, memoryview)):
                text_content = bytes(content).decode('utf-8', errors='replace')
            else:
                text_content = str(content) if content else ""

            if len(text_content.strip()) < 10:
                return blob_id, None, "Content too short"

            # Use filename + first 400 chars (bge-large has 512 token context)
            embed_text = f"{file_name or file_path or 'unknown'}\n{text_content[:400]}"

        node = get_node()

        resp = httpx.post(
            f"{node['host']}/api/embed",
            json={"model": EMBED_MODEL, "input": embed_text},
            timeout=30.0
        )
        resp.raise_for_status()
        data = resp.json()
        embeddings = data.get("embeddings", [[]])

        if not embeddings or not embeddings[0]:
            return blob_id, None, "Empty embedding"

        vector = embeddings[0]
        return blob_id, vector, None

    except Exception as e:
        return blob_id, None, str(e)


def main():
    print("=== Embedding Backfill (bge-large 1024d) ===")
    print(f"Model: {EMBED_MODEL}")
    print(f"Workers: {MAX_WORKERS}, Batch: {BATCH_SIZE}")
    print(f"Strategy: summary-first, content-fallback")
    print()

    # Verify Ollama on all nodes
    seen_hosts = set()
    active_nodes = []
    for node in OLLAMA_NODES:
        if node['host'] in seen_hosts:
            active_nodes.append(node)
            continue
        seen_hosts.add(node['host'])
        try:
            r = httpx.get(f"{node['host']}/api/tags", timeout=5.0)
            models = [m['name'] for m in r.json().get('models', [])]
            if EMBED_MODEL in models:
                print(f"  ✅ {node['label']} — {EMBED_MODEL} available")
                active_nodes.append(node)
            else:
                print(f"  ⚠️  {node['label']} — pulling {EMBED_MODEL}...")
                httpx.post(f"{node['host']}/api/pull", json={"name": EMBED_MODEL}, timeout=300.0)
                active_nodes.append(node)
        except Exception as e:
            print(f"  ❌ {node['label']} — {e}")

    if not active_nodes:
        print("ERROR: No Ollama nodes available!")
        return

    global _node_cycle
    _node_cycle = itertools.cycle(active_nodes)

    # Count
    db = SessionLocal()
    total_missing = db.execute(
        text("SELECT count(*) FROM blobs WHERE embedding IS NULL AND content IS NOT NULL")
    ).scalar()
    print(f"\nBlobs needing embeddings: {total_missing:,}")
    db.close()

    if total_missing == 0:
        print("All blobs have embeddings, but we will wait for new ones...")

    total_processed = 0
    total_success = 0
    total_failed = 0
    start_time = time.time()

    while True:
        db = SessionLocal()

        # Prioritize blobs that have summaries (better embedding quality)
        rows = db.execute(text("""
            SELECT id, summary, file_name, file_path, content
            FROM blobs
            WHERE embedding IS NULL AND content IS NOT NULL
            ORDER BY
                CASE WHEN summary IS NOT NULL THEN 0 ELSE 1 END,
                size ASC
            LIMIT :limit
        """), {"limit": BATCH_SIZE}).fetchall()

        if not rows:
            db.close()
            time.sleep(10)
            continue

        batch_success = 0
        batch_failed = 0

        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(embed_blob, row[0], row[1], row[2], row[3], row[4]): row[0]
                for row in rows
            }

            for future in concurrent.futures.as_completed(futures):
                blob_id, vector, err = future.result()

                if vector:
                    try:
                        vec_str = '[' + ','.join(str(v) for v in vector) + ']'
                        db.execute(
                            text("UPDATE blobs SET embedding = CAST(:vec AS vector) WHERE id = :id"),
                            {"vec": vec_str, "id": blob_id}
                        )
                        db.commit()
                        batch_success += 1
                    except Exception as db_err:
                        db.rollback()
                        batch_failed += 1
                else:
                    batch_failed += 1
                    if batch_failed <= 3:
                        print(f"  Fail [{blob_id[:8]}]: {err}")

        total_processed += len(rows)
        total_success += batch_success
        total_failed += batch_failed
        elapsed = time.time() - start_time
        rate = total_success / elapsed if elapsed > 0 else 0

        remaining = total_missing - total_success
        eta_min = (remaining / rate / 60) if rate > 0 else 0

        # Count how many used summaries vs content
        summary_count = sum(1 for r in rows if r[1])
        percent = (total_success / total_missing * 100) if total_missing > 0 else 100.0
        print(f"Batch: +{batch_success} ok, +{batch_failed} fail | "
              f"Total: {total_success:,}/{total_missing:,} ({percent:.1f}%) | "
              f"Rate: {rate:.1f}/s | ETA: {eta_min:.0f}min | "
              f"Summary-based: {summary_count}/{len(rows)}")

        db.close()

    elapsed = time.time() - start_time
    print(f"\n=== Embedding Backfill Complete ===")
    print(f"Processed: {total_processed:,} | Success: {total_success:,} | Failed: {total_failed:,}")
    print(f"Time: {elapsed/60:.1f} minutes ({elapsed/3600:.1f} hours)")


if __name__ == "__main__":
    main()
