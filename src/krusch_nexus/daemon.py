"""
KruschNexus Watchdog Ingest Daemon & Stale-Lock Reaper (daemon.py)
================================================================
Monitors watch directories for incoming files, moves them into staging
with .part locks, executes ingestion with separate worker concurrency bounds,
and runs a background stale-lock reaper to prevent deadlocks from crashes.
"""

import os
import sys
import time
import shutil
import asyncio
import logging
from pathlib import Path
from typing import Optional

from .config import NexusConfig
from .ingest import IngestPipeline, ALLOWED_EXT

logger = logging.getLogger("krusch_nexus.daemon")


def get_default_watch_dir() -> str:
    """Resolve default watch directory: container (/app/ingest_watch) or repo root."""
    if os.path.exists("/app/ingest_watch"):
        return "/app/ingest_watch"
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ingest_watch"))


async def reap_stale_locks(staging_dir: str, timeout_seconds: float = 600.0):
    """
    Periodically scan staging directory for abandoned .part files older than
    timeout_seconds and isolate them to .failed/stale_locks/ to prevent deadlocks.
    """
    while True:
        try:
            now = time.time()
            if os.path.exists(staging_dir):
                for root, _, files in os.walk(staging_dir):
                    for f in files:
                        if f.endswith(".part"):
                            f_path = os.path.join(root, f)
                            try:
                                mtime = os.path.getmtime(f_path)
                                if now - mtime > timeout_seconds:
                                    logger.warning(
                                        f"Stale lock detected on '{f}' (age: {int(now - mtime)}s > {int(timeout_seconds)}s). "
                                        "Reaping and quarantining abandoned file."
                                    )
                                    ws_name = os.path.basename(root)
                                    base_dir = Path(staging_dir).parent
                                    failed_dir = base_dir / ".failed" / ws_name
                                    failed_dir.mkdir(parents=True, exist_ok=True)
                                    clean_name = f.replace(".part", "")
                                    dest = failed_dir / f"stale_{clean_name}"
                                    shutil.move(f_path, str(dest))
                            except Exception as e:
                                logger.debug(f"Error checking file {f_path}: {e}")
        except Exception as e:
            logger.error(f"Error during stale-lock reaper pass: {e}")

        await asyncio.sleep(60)  # Check every 60 seconds


async def run_daemon(watch_dir: Optional[str] = None, config: Optional[NexusConfig] = None):
    """
    Continuous watch daemon monitoring watch_dir with:
    - Staging with .part rename lock
    - Separate OCR vs Embed queue bounding
    - Background stale-lock reaper
    - Poison file isolation
    """
    conf = config or NexusConfig.from_env()
    target_watch_dir = watch_dir or conf.watch_dir or get_default_watch_dir()
    os.makedirs(target_watch_dir, exist_ok=True)
    staging_dir = os.path.join(target_watch_dir, "staging")
    os.makedirs(staging_dir, exist_ok=True)

    if target_watch_dir not in conf.allowed_ingest_roots:
        conf.allowed_ingest_roots.append(os.path.abspath(target_watch_dir))

    pipeline = IngestPipeline(conf)
    ocr_semaphore = asyncio.Semaphore(conf.max_ocr_workers)

    logger.info(f"Starting KruschNexus Ingest Daemon on: {target_watch_dir}")
    logger.info(f"Workers: {conf.max_ocr_workers} OCR, {conf.max_embed_workers} Embed. Staging: {staging_dir}")

    # Launch background stale-lock reaper
    reaper_task = asyncio.create_task(reap_stale_locks(staging_dir, timeout_seconds=600.0))

    async def _safe_process(source_path: str, ws_name: str, fname: str):
        async with ocr_semaphore:
            ws_staging = os.path.join(staging_dir, ws_name)
            os.makedirs(ws_staging, exist_ok=True)
            part_path = os.path.join(ws_staging, f"{int(time.time())}_{fname}.part")

            try:
                if not os.path.exists(source_path):
                    return
                shutil.move(source_path, part_path)
            except Exception as e:
                logger.debug(f"Could not lock file {fname} into staging: {e}")
                return

            await asyncio.to_thread(
                pipeline.process_file,
                filepath=part_path,
                workspace_name=ws_name,
                archive_source=True,
                filename=fname
            )

    try:
        while True:
            try:
                for item in sorted(os.listdir(target_watch_dir)):
                    if item.startswith('.') or item in [".ingested", ".failed", "staging"]:
                        continue

                    item_path = os.path.join(target_watch_dir, item)

                    if os.path.isdir(item_path):
                        ws_name = item
                        for fname in sorted(os.listdir(item_path)):
                            if fname.startswith('.') or fname.endswith('.part'):
                                continue
                            f_path = os.path.join(item_path, fname)
                            if os.path.isfile(f_path) and fname.lower().endswith(ALLOWED_EXT):
                                asyncio.create_task(_safe_process(f_path, ws_name, fname))
                    elif os.path.isfile(item_path) and item.lower().endswith(ALLOWED_EXT):
                        asyncio.create_task(_safe_process(item_path, "General", item))

            except Exception as e:
                logger.error(f"Watch daemon sweep error: {e}")

            await asyncio.sleep(2)
    finally:
        reaper_task.cancel()


def main():
    """CLI entrypoint for nexus-daemon."""
    asyncio.run(run_daemon())


if __name__ == "__main__":
    main()
