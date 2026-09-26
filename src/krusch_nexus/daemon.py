"""
KruschNexus Watchdog Ingest Daemon & Stale-Lock Reaper (daemon.py)
================================================================
Monitors watch directories for incoming files, moves them into staging
with .part locks, executes ingestion with separate worker concurrency bounds,
and runs a background stale-lock reaper to prevent deadlocks from crashes.
"""

import os
import time
import shutil
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import NexusConfig
from .ingest import IngestPipeline, ALLOWED_EXT, reap_stale_locks

logger = logging.getLogger("krusch_nexus.daemon")


def get_default_watch_dir() -> str:
    """Resolve default watch directory: container (/app/ingest_watch) or repo root."""
    if os.path.exists("/app/ingest_watch"):
        return "/app/ingest_watch"
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ingest_watch"))


async def _run_periodic_reaper(staging_dir: str, timeout_seconds: float = 600.0):
    """Periodically execute the standalone reap_stale_locks function."""
    while True:
        try:
            reap_stale_locks(staging_dir, timeout_seconds=timeout_seconds)
        except Exception as e:
            logger.error(f"Error during periodic stale lock reaper pass: {e}")
        await asyncio.sleep(60)


_RETRY_TRACKER: dict = {}


async def run_daemon(watch_dir: Optional[str] = None, config: Optional[NexusConfig] = None):
    """
    Continuous watch daemon monitoring watch_dir with:
    - Staging with .part rename lock
    - Separate OCR vs Embed queue bounding
    - Background stale-lock reaper
    - Idempotent retries with max 3 attempts before quarantine
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

    # Launch background stale-lock reaper task
    reaper_task = asyncio.create_task(_run_periodic_reaper(staging_dir, timeout_seconds=conf.stale_lock_timeout_seconds))

    daemon_status_path = Path(target_watch_dir) / ".daemon_status.json"
    daemon_state = {
        "watch_dir": str(target_watch_dir),
        "queue_depth": 0,
        "current_file": None,
        "last_processed_file": None,
        "last_failure": None,
        "updated_at": datetime.now(timezone.utc).isoformat()
    }

    def _sync_status():
        try:
            daemon_state["updated_at"] = datetime.now(timezone.utc).isoformat()
            with open(daemon_status_path, "w", encoding="utf-8") as f:
                json.dump(daemon_state, f, indent=2)
        except Exception:
            pass

    async def _safe_process(source_path: str, ws_name: str, fname: str):
        file_key = f"{ws_name}/{fname}"
        daemon_state["current_file"] = file_key
        _sync_status()
        attempts = _RETRY_TRACKER.get(file_key, 0) + 1
        _RETRY_TRACKER[file_key] = attempts

        if attempts > 3:
            logger.error(f"File '{fname}' in '{ws_name}' exceeded max attempts (3). Quarantining.")
            daemon_state["last_failure"] = {"file": file_key, "error": "MaxRetriesExceeded", "time": datetime.now(timezone.utc).isoformat()}
            _sync_status()
            failed_dir = Path(staging_dir).parent / ".failed" / ws_name
            failed_dir.mkdir(parents=True, exist_ok=True)
            dest = failed_dir / f"quarantined_{fname}"
            try:
                if os.path.exists(source_path):
                    shutil.move(source_path, str(dest))
                with open(failed_dir / f"quarantined_{fname}.error.json", "w", encoding="utf-8") as f:
                    import json
                    json.dump({"error": "MaxRetriesExceeded", "attempts": attempts, "status": "quarantined"}, f, indent=2)
            except Exception as q_err:
                logger.warning(f"Could not quarantine poison file: {q_err}")
            _RETRY_TRACKER.pop(file_key, None)
            return

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

            report = await asyncio.to_thread(
                pipeline.process_file,
                filepath=part_path,
                workspace_name=ws_name,
                archive_source=True,
                filename=fname
            )
            if report.status == "completed":
                _RETRY_TRACKER.pop(file_key, None)
                daemon_state["last_processed_file"] = file_key
                daemon_state["current_file"] = None
                _sync_status()
            elif report.status == "failed":
                daemon_state["last_failure"] = {"file": file_key, "error": report.error, "time": datetime.now(timezone.utc).isoformat()}
                daemon_state["current_file"] = None
                _sync_status()

    try:
        while True:
            try:
                pending_files = []
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
                                pending_files.append((f_path, ws_name, fname))
                    elif os.path.isfile(item_path) and item.lower().endswith(ALLOWED_EXT):
                        pending_files.append((item_path, "General", item))

                daemon_state["queue_depth"] = len(pending_files)
                _sync_status()

                for f_path, ws_name, fname in pending_files:
                    asyncio.create_task(_safe_process(f_path, ws_name, fname))

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
