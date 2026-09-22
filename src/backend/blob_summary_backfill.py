#!/usr/bin/env python3
"""
Blob Summary Backfill for kruschdb
Generates AI summaries for blobs missing them using Ollama on kruschdev (dual RTX 3060, 24GB VRAM).
Targets: kruschdb.blobs.summary (419K+ rows missing)
"""

import os
import json
import time
import httpx
import traceback
import concurrent.futures
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Configuration — kruschdb via openclaw-db container on kruschserv:5434
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://openclaw:openclaw_password@10.0.0.85:5434/kruschdb")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "500"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "16"))

# 3 parallel 14b endpoints — weighted round-robin (2080 Ti gets 2× share — faster bandwidth)
OLLAMA_NODES = [
    {"host": "http://10.0.0.144:11434", "model": "qwen2.5-coder:14b", "label": "kruschdev GPU0 (RTX 3060 #0)"},
    {"host": "http://10.0.0.85:11434",  "model": "qwen2.5-coder:14b", "label": "kruschserv (RTX 2080 Ti)"},
    {"host": "http://10.0.0.144:11435", "model": "qwen2.5-coder:14b", "label": "kruschdev GPU1 (RTX 3060 #1)"},
    {"host": "http://10.0.0.85:11434",  "model": "qwen2.5-coder:14b", "label": "kruschserv (RTX 2080 Ti)"},
]

import itertools
_node_cycle = itertools.cycle(OLLAMA_NODES)

def get_ollama_node():
    """Round-robin across nodes, each with its own model."""
    return next(_node_cycle)

engine = create_engine(DATABASE_URL, pool_size=MAX_WORKERS + 2, max_overflow=5)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

PROMPT = """You are a code librarian. Given a source code file, output a JSON object with:
- "summary": A single sentence (max 120 chars) describing what this file does.
- "tags": An array of 3-5 lowercase tags categorizing the file.

Rules:
- Summary must be specific about the technology, pattern, or feature.
- Output ONLY valid JSON, no markdown fences.
- If the file is too small or trivial (e.g., empty config), use: {{"summary":"Minimal config file","tags":["config"]}}

Example: {{"summary":"Express middleware for JWT auth with RBAC","tags":["auth","jwt","middleware","express"]}}

File: {filename}

{text}"""


def generate_summary(blob_id, file_name, file_path, content):
    """Call Ollama to generate a summary for a single blob."""
    try:
        # Decode content (stored as bytea)
        if isinstance(content, (bytes, memoryview)):
            text_content = bytes(content).decode('utf-8', errors='replace')
        else:
            text_content = str(content) if content else ""

        # Skip empty/tiny files
        if len(text_content.strip()) < 10:
            return blob_id, "Minimal or empty file", None

        sample = text_content[:4000]
        prompt = PROMPT.format(filename=file_name or file_path or "unknown", text=sample)

        # Try up to 2 times (retry on timeout)
        for attempt in range(2):
            try:
                node = get_ollama_node()
                resp = httpx.post(
                    f"{node['host']}/api/generate",
                    json={
                        "model": node['model'],
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.1, "num_predict": 200}
                    },
                    timeout=90.0
                )
                resp.raise_for_status()
                raw = resp.json().get("response", "").strip()
                break
            except (httpx.TimeoutException, httpx.HTTPStatusError):
                if attempt == 1:
                    return blob_id, None, "Timeout after retry"
                continue

        # Clean markdown fences
        if raw.startswith("```json"):
            raw = raw[7:]
        if raw.startswith("```"):
            raw = raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

        # Find last closing brace
        last_brace = raw.rfind('}')
        if last_brace > 0:
            raw = raw[:last_brace + 1]

        # Try JSON parse
        try:
            data = json.loads(raw)
            summary = str(data.get("summary", ""))[:200]
        except json.JSONDecodeError:
            # Regex fallback — extract summary field from malformed JSON
            import re
            m = re.search(r'"summary"\s*:\s*"([^"]+)"', raw)
            if m:
                summary = m.group(1)[:200]
            else:
                # Last resort — use first line of response as summary
                summary = raw.split('\n')[0][:200] if raw else None

        if not summary:
            return blob_id, None, "Empty summary from model"

        return blob_id, summary, None

    except Exception as e:
        return blob_id, None, str(e)


def main():
    print(f"=== Blob Summary Backfill ===")
    print(f"Nodes:")
    for n in OLLAMA_NODES:
        print(f"  {n['label']} → {n['model']}")
    print(f"DB:    {DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else DATABASE_URL}")
    print(f"Workers: {MAX_WORKERS}, Batch: {BATCH_SIZE}")
    print()

    # Verify Ollama connectivity on all hosts
    active_nodes = []
    for node in OLLAMA_NODES:
        try:
            r = httpx.get(f"{node['host']}/api/tags", timeout=5.0)
            models = [m['name'] for m in r.json().get('models', [])]
            if node['model'] in models:
                print(f"  ✅ {node['label']} — {node['model']} available")
                active_nodes.append(node)
            else:
                print(f"  ⚠️  {node['label']} — {node['model']} not found, pulling...")
                httpx.post(f"{node['host']}/api/pull", json={"name": node['model']}, timeout=600.0)
                active_nodes.append(node)
        except Exception as e:
            print(f"  ❌ {node['label']} — unreachable: {e}")

    if not active_nodes:
        print("ERROR: No Ollama nodes available!")
        return

    # Reset cycle with only active nodes
    global _node_cycle
    _node_cycle = itertools.cycle(active_nodes)

    # Verify DB connectivity
    try:
        db = SessionLocal()
        count = db.execute(text("SELECT count(*) FROM blobs WHERE summary IS NULL")).scalar()
        print(f"Blobs needing summaries: {count:,}")
        db.close()
    except Exception as e:
        print(f"ERROR: Cannot connect to DB: {e}")
        return

    total_processed = 0
    total_success = 0
    total_failed = 0
    start_time = time.time()

    while True:
        db = SessionLocal()

        # Fetch a batch of blobs without summaries
        rows = db.execute(text("""
            SELECT id, file_name, file_path, content
            FROM blobs
            WHERE summary IS NULL AND content IS NOT NULL
            ORDER BY size ASC
            LIMIT :limit
        """), {"limit": BATCH_SIZE}).fetchall()

        if not rows:
            print("All blobs have summaries!")
            db.close()
            break

        batch_success = 0
        batch_failed = 0

        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(generate_summary, row[0], row[1], row[2], row[3]): row[0]
                for row in rows
            }

            for future in concurrent.futures.as_completed(futures):
                blob_id, summary, err = future.result()

                if summary:
                    try:
                        db.execute(
                            text("UPDATE blobs SET summary = :summary WHERE id = :id"),
                            {"summary": summary, "id": blob_id}
                        )
                        db.commit()
                        batch_success += 1
                    except Exception as db_err:
                        db.rollback()
                        print(f"  DB error for {blob_id}: {db_err}")
                        batch_failed += 1
                else:
                    batch_failed += 1

        total_processed += len(rows)
        total_success += batch_success
        total_failed += batch_failed
        elapsed = time.time() - start_time
        rate = total_processed / elapsed if elapsed > 0 else 0

        remaining = count - total_processed
        eta_min = (remaining / rate / 60) if rate > 0 else 0

        print(f"Batch done: +{batch_success} ok, +{batch_failed} fail | "
              f"Total: {total_processed:,}/{count:,} ({total_processed/count*100:.1f}%) | "
              f"Rate: {rate:.1f}/s | ETA: {eta_min:.0f}min")

        db.close()

    elapsed = time.time() - start_time
    print(f"\n=== Backfill Complete ===")
    print(f"Processed: {total_processed:,} | Success: {total_success:,} | Failed: {total_failed:,}")
    print(f"Time: {elapsed/60:.1f} minutes ({elapsed/3600:.1f} hours)")


if __name__ == "__main__":
    main()
