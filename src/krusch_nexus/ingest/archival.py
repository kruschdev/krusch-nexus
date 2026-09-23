"""
KruschNexus Archival & Poison Isolation (archival.py)
=====================================================
Post-commit content-addressed file archival and failure isolation with redacted error sidecars.
"""

import os
import json
import time
import shutil
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from ..models import IngestState
from ..store import IngestRun, get_db_session

logger = logging.getLogger("krusch_nexus.ingest.archival")


def archive_success(
    filepath: str,
    filename: str,
    workspace_name: str,
    file_hash: str,
    manifest: Optional[Dict[str, Any]] = None
):
    """
    Move successfully processed file to .ingested/<workspace>/<hash[:12]>_<name>
    and write manifest JSON.
    """
    try:
        p = Path(filepath)
        parent = p.parent
        if parent.name == "staging" or parent.parent.name == "staging":
            base_dir = parent.parent if parent.name == "staging" else parent.parent.parent
        else:
            base_dir = parent

        ingested_dir = base_dir / ".ingested" / workspace_name
        ingested_dir.mkdir(parents=True, exist_ok=True)
        dest = ingested_dir / f"{file_hash[:12]}_{filename}"

        if p.exists() and p.resolve() != dest.resolve():
            shutil.move(str(p), str(dest))

        if manifest:
            manifest_file = ingested_dir / f"{file_hash[:12]}_{filename}.manifest.json"
            with open(manifest_file, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not move '{filename}' to .ingested: {e}")


def handle_failure(
    filepath: str,
    filename: str,
    workspace_name: str,
    error: Exception,
    start_time: float,
    file_hash: str = "",
    archive: bool = False,
    run_rec_id: Optional[int] = None,
    engine=None
):
    """
    Isolate failed file to .failed/<workspace>/<name> and write redacted sidecar .error.json.
    Never leaves document chunks, raw file text, or credentials in the error JSON.
    """
    try:
        p = Path(filepath)
        parent = p.parent
        if "staging" in str(parent):
            base_dir = parent
            while base_dir.name in ["staging", workspace_name]:
                base_dir = base_dir.parent
        else:
            base_dir = parent

        failed_dir = base_dir / ".failed" / workspace_name
        failed_dir.mkdir(parents=True, exist_ok=True)

        dest_file = failed_dir / filename
        sidecar_file = failed_dir / f"{filename}.error.json"

        if archive and p.exists() and p.resolve() != dest_file.resolve():
            shutil.move(str(p), str(dest_file))

        # Redacted error data: Zero document content or chunk text!
        sidecar_data = {
            "filename": filename,
            "workspace": workspace_name,
            "file_hash": file_hash,
            "error_class": type(error).__name__,
            "error_message": str(error),
            "duration_ms": round((time.time() - start_time) * 1000, 2),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        with open(sidecar_file, "w", encoding="utf-8") as f:
            json.dump(sidecar_data, f, indent=2)

        # Persist failure to IngestRun ledger
        if engine:
            try:
                with get_db_session(engine) as db_sess:
                    if run_rec_id:
                        rec = db_sess.query(IngestRun).filter(IngestRun.id == run_rec_id).first()
                        if rec:
                            rec.state = IngestState.FAILED.value
                            rec.error_class = type(error).__name__
                            rec.error_message = str(error)
                            rec.completed_at = datetime.now(timezone.utc)
                            rec.duration_ms = round((time.time() - start_time) * 1000, 2)
                            db_sess.commit()
                            logger.info(f"Isolated poison file '{filename}' ({type(error).__name__}) to {failed_dir}")
                            return

                    run_rec = IngestRun(
                        filename=filename,
                        workspace=workspace_name,
                        workspace_name=workspace_name,
                        file_hash=file_hash,
                        state=IngestState.FAILED.value,
                        started_at=datetime.fromtimestamp(start_time, timezone.utc),
                        completed_at=datetime.now(timezone.utc),
                        duration_ms=round((time.time() - start_time) * 1000, 2),
                        error_class=type(error).__name__,
                        error_message=str(error)
                    )
                    db_sess.add(run_rec)
                    db_sess.commit()
            except Exception as dbe:
                logger.debug(f"Could not persist failed IngestRun: {dbe}")

        logger.info(f"Isolated poison file '{filename}' ({type(error).__name__}) to {failed_dir}")
    except Exception as e:
        logger.error(f"Error isolating failed file '{filename}': {e}")


def list_poison_files(
    watch_dir: Optional[str] = None,
    workspace: Optional[str] = None,
    allowed_roots: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """
    List all failed/poison files across workspaces in .failed/ with error sidecar metadata.
    """
    candidate_bases = []
    if watch_dir:
        candidate_bases.append(Path(watch_dir).resolve() / ".failed")
        candidate_bases.append(Path(watch_dir).parent.resolve() / ".failed")
    if allowed_roots:
        for r in allowed_roots:
            candidate_bases.append(Path(r).resolve() / ".failed")
    candidate_bases.append(Path("./ingest_watch").resolve() / ".failed")
    candidate_bases.append(Path(".failed").resolve())

    found_bases = [b for b in candidate_bases if b.exists() and b.is_dir()]
    if not found_bases:
        return []

    results = []
    seen_paths = set()
    for failed_base in found_bases:
        target_dirs = [failed_base / workspace] if workspace else [d for d in failed_base.iterdir() if d.is_dir()]
        for ws_dir in target_dirs:
            if not ws_dir.exists() or not ws_dir.is_dir():
                continue
            ws_name = ws_dir.name
            for item in ws_dir.iterdir():
                if item.is_file() and not item.name.endswith(".error.json"):
                    if str(item) in seen_paths:
                        continue
                    seen_paths.add(str(item))
                    sidecar = ws_dir / f"{item.name}.error.json"
                    sidecar_data = {}
                    if sidecar.exists():
                        try:
                            with open(sidecar, "r", encoding="utf-8") as f:
                                sidecar_data = json.load(f)
                        except Exception:
                            pass
                    results.append({
                        "filename": item.name,
                        "workspace": ws_name,
                        "filepath": str(item),
                        "file_size": item.stat().st_size,
                        "mtime": datetime.fromtimestamp(item.stat().st_mtime, timezone.utc).isoformat(),
                        "error_class": sidecar_data.get("error_class", "UnknownError"),
                        "error_message": sidecar_data.get("error_message", ""),
                        "duration_ms": sidecar_data.get("duration_ms", 0.0),
                        "failed_at": sidecar_data.get("timestamp", "")
                    })
    return sorted(results, key=lambda x: x.get("failed_at", ""), reverse=True)


def replay_poison_file(
    filename: str,
    workspace_name: str,
    pipeline,
    watch_dir: Optional[str] = None,
    allowed_roots: Optional[List[str]] = None,
    doc_type: Any = None
) -> Any:
    """
    Replay a poisoned file from .failed/<workspace>/<filename>.
    If ingestion succeeds, remove the file and its .error.json sidecar from .failed/.
    """
    from ..models import DocType
    resolved_doc_type = doc_type or DocType.GENERAL

    candidate_bases = []
    if watch_dir:
        candidate_bases.append(Path(watch_dir).resolve() / ".failed")
        candidate_bases.append(Path(watch_dir).parent.resolve() / ".failed")
    if allowed_roots:
        for r in allowed_roots:
            candidate_bases.append(Path(r).resolve() / ".failed")
    candidate_bases.append(Path("./ingest_watch").resolve() / ".failed")
    candidate_bases.append(Path(".failed").resolve())

    target_path = None
    for b in candidate_bases:
        candidate = b / workspace_name / filename
        if candidate.exists():
            target_path = candidate
            break

    if not target_path or not target_path.exists():
        raise FileNotFoundError(f"Poison file '{filename}' not found in workspace '{workspace_name}'")

    report = pipeline.process_file(
        filepath=str(target_path),
        workspace_name=workspace_name,
        doc_type=resolved_doc_type,
        archive_source=False
    )

    if report.status in ("completed", "skipped_duplicate"):
        try:
            target_path.unlink(missing_ok=True)
            sidecar = target_path.parent / f"{filename}.error.json"
            sidecar.unlink(missing_ok=True)
            logger.info(f"Successfully replayed and cleared poison file '{filename}' from .failed/{workspace_name}")
        except Exception as e:
            logger.warning(f"Could not remove replayed poison file from .failed: {e}")

    return report
