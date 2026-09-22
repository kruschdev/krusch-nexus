#!/usr/bin/env python3
"""
Blob Summary Backfill — Gemini 2.0 Flash Lite Edition
Uses Google's Gemini Flash Lite for high-throughput code summarization.
~50-100× faster than local Ollama at ~$35 total cost for 400K blobs.
"""

import os
import json
import time
import asyncio
import aiohttp
import traceback
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Configuration
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://openclaw:openclaw_password@10.0.0.85:5434/kruschdb")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL = "gemini-2.5-flash-lite"
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "500"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "20"))  # Burst-tested sweet spot: 16/s, 0 rate limits
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

engine = create_engine(DATABASE_URL, pool_size=5, max_overflow=5)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def build_prompt(filename, text_content):
    """Build the prompt using concatenation to avoid format string issues."""
    return ('You are a code librarian. Given a source code file, output a JSON object with:\n'
            '- "summary": A single sentence (max 120 chars) describing what this file does.\n'
            '- "tags": An array of 3-5 lowercase tags categorizing the file.\n'
            '\n'
            'Rules:\n'
            '- Summary must be specific about the technology, pattern, or feature.\n'
            '- Output ONLY valid JSON, no markdown fences.\n'
            '- If the file is too small or trivial, use: {"summary":"Minimal config file","tags":["config"]}\n'
            '\n'
            'Example: {"summary":"Express middleware for JWT auth with RBAC","tags":["auth","jwt","middleware","express"]}\n'
            '\n'
            f'File: {filename}\n'
            '\n'
            f'{text_content}')


async def generate_summary(session, blob_id, file_name, file_path, content, semaphore):
    """Call Gemini Flash Lite to generate a summary."""
    async with semaphore:
        try:
            # Decode content
            if isinstance(content, (bytes, memoryview)):
                text_content = bytes(content).decode('utf-8', errors='replace')
            else:
                text_content = str(content) if content else ""

            if len(text_content.strip()) < 10:
                return blob_id, "Minimal or empty file", None

            # Trivial files don't need LLM — auto-tag
            if len(text_content.strip()) < 200:
                ext = (file_name or '').rsplit('.', 1)[-1] if file_name else 'unknown'
                return blob_id, f"Minimal {ext} file", None

            sample = text_content[:6000]
            prompt = build_prompt(file_name or file_path or "unknown", sample)

            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "maxOutputTokens": 200
                }
            }

            for attempt in range(3):
                try:
                    headers = {"X-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}
                    async with session.post(API_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                        if resp.status == 429:
                            wait = 5 * (attempt + 1)
                            await asyncio.sleep(wait)
                            continue
                        if resp.status == 503:
                            await asyncio.sleep(3)
                            continue
                        if resp.status != 200:
                            body = await resp.text()
                            return blob_id, None, f"HTTP {resp.status}: {body[:100]}"
                        resp.raise_for_status()
                        data = await resp.json()

                    raw = data["candidates"][0]["content"]["parts"][0]["text"].strip()

                    # Clean markdown fences
                    if raw.startswith("```json"):
                        raw = raw[7:]
                    if raw.startswith("```"):
                        raw = raw[3:]
                    if raw.endswith("```"):
                        raw = raw[:-3]
                    raw = raw.strip()

                    last_brace = raw.rfind('}')
                    if last_brace > 0:
                        raw = raw[:last_brace + 1]

                    parsed = json.loads(raw)
                    summary = str(parsed.get("summary", ""))[:200]

                    if not summary:
                        return blob_id, None, "Empty summary"

                    # Quality gate: reject code snippets stored as summaries
                    if _is_code(summary):
                        return blob_id, None, "Summary looks like code"

                    return blob_id, summary, None

                except (aiohttp.ClientError, asyncio.TimeoutError):
                    if attempt == 2:
                        return blob_id, None, "Timeout after 3 retries"
                    await asyncio.sleep(1)
                except json.JSONDecodeError:
                    # Regex fallback
                    import re
                    m = re.search(r'"summary"\s*:\s*"([^"]+)"', raw)
                    if m:
                        s = m.group(1)[:200]
                        if not _is_code(s):
                            return blob_id, s, None
                    if attempt == 2:
                        return blob_id, None, "JSON parse error"
                    continue

            return blob_id, None, "Max retries exceeded"

        except Exception as e:
            return blob_id, None, str(e)


def _is_code(text):
    """Reject text that looks like code, not a summary."""
    t = text.strip()
    # Multi-line = code, not a summary
    if '\n' in t or '\r' in t or t.count('+') > 3:
        return True
    if len(t) > 150:
        return True
    code_starts = ('import ', 'from ', '# ', '//', '/*', 'const ', 'let ', 'var ',
                   'def ', 'class ', 'export ', 'module.', 'require(', '{', '<',
                   'package ', 'using ', '#include', 'SPDX-', '#!/')
    return t.startswith(code_starts)


async def process_batch(rows, db):
    """Process a batch of blobs concurrently."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    batch_success = 0
    batch_failed = 0

    async with aiohttp.ClientSession() as session:
        tasks = [
            generate_summary(session, row[0], row[1], row[2], row[3], semaphore)
            for row in rows
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        err_shown = 0
        for result in results:
            if isinstance(result, Exception):
                batch_failed += 1
                if err_shown < 3:
                    print(f"  Exception: {result}")
                    err_shown += 1
                continue

            if not result or not isinstance(result, tuple) or len(result) != 3:
                batch_failed += 1
                if err_shown < 3:
                    print(f"  Fail: Invalid task result: {result}")
                    err_shown += 1
                continue

            blob_id, summary, err = result

            if summary:
                try:
                    db.execute(
                        text("UPDATE blobs SET summary = :summary, embedding = NULL WHERE id = :id"),
                        {"summary": summary, "id": blob_id}
                    )
                    db.commit()
                    batch_success += 1
                except Exception as db_err:
                    db.rollback()
                    batch_failed += 1
            else:
                batch_failed += 1
                if err_shown < 3:
                    print(f"  Fail [{blob_id[:8]}]: {err}")
                    err_shown += 1
                # Mark persistent failures so they aren't re-queried
                try:
                    db.execute(
                        text("UPDATE blobs SET summary = :summary WHERE id = :id"),
                        {"summary": "[SKIP]", "id": blob_id}
                    )
                    db.commit()
                except Exception:
                    db.rollback()

    return batch_success, batch_failed


def main():
    if not GEMINI_API_KEY:
        print("ERROR: GEMINI_API_KEY not set")
        return

    print(f"=== Blob Summary Backfill (Gemini Flash Lite) ===")
    print(f"Model: {MODEL}")
    print(f"Concurrency: {MAX_CONCURRENT}")
    print(f"Batch size: {BATCH_SIZE}")
    print()

    # Get total count
    db = SessionLocal()
    total_missing = db.execute(
        text("SELECT count(*) FROM blobs WHERE summary IS NULL AND content IS NOT NULL")
    ).scalar()
    print(f"Blobs needing summaries: {total_missing:,}")
    db.close()

    if total_missing == 0:
        print("All blobs have summaries!")
        return

    total_processed = 0
    total_success = 0
    total_failed = 0
    start_time = time.time()

    while True:
        db = SessionLocal()

        rows = db.execute(text("""
            SELECT id, file_name, file_path, content
            FROM blobs
            WHERE summary IS NULL AND content IS NOT NULL
            ORDER BY size ASC
            LIMIT :limit
        """), {"limit": BATCH_SIZE}).fetchall()

        if not rows:
            print("\nAll blobs have summaries!")
            db.close()
            break

        batch_success, batch_failed = asyncio.run(process_batch(rows, db))

        total_processed += len(rows)
        total_success += batch_success
        total_failed += batch_failed
        elapsed = time.time() - start_time
        rate = total_success / elapsed if elapsed > 0 else 0

        remaining = total_missing - total_success
        eta_min = (remaining / rate / 60) if rate > 0 else 0

        print(f"Batch: +{batch_success} ok, +{batch_failed} fail | "
              f"Total: {total_success:,}/{total_missing:,} ({total_success/total_missing*100:.1f}%) | "
              f"Rate: {rate:.1f}/s | ETA: {eta_min:.0f}min")

        db.close()

        # Pace between batches to avoid rate limits
        time.sleep(1)

    elapsed = time.time() - start_time
    print(f"\n=== Backfill Complete ===")
    print(f"Processed: {total_processed:,} | Success: {total_success:,} | Failed: {total_failed:,}")
    print(f"Time: {elapsed/60:.1f} minutes ({elapsed/3600:.1f} hours)")


if __name__ == "__main__":
    main()
