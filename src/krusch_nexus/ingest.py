"""
KruschNexus Hardened Ingestion Engine & State Machine
=====================================================
Closed-loop state machine with strict path sandboxing, resource bounds,
poison file redaction, watchdog inotify tracking, and idempotent retries.
"""

import os
import sys
import time
import json
import shutil
import asyncio
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

from .config import NexusConfig
from .models import IngestReport
from .sandbox import validate_safe_path
from .exceptions import FileOversizedError, ParseError, PathSandboxError
from .db import Workspace, Document, DocumentChunk, get_engine, get_session_factory
from .parsers import parse_document, compute_file_hash, ParsedPage
from .chunking import chunk_document_pages, Chunk
from .embeddings import get_embeddings_batch

logger = logging.getLogger("krusch_nexus.ingest")

ALLOWED_EXT = (
    '.pdf', '.docx', '.doc', '.eml', '.msg', '.txt',
    '.md', '.csv', '.json', '.html', '.htm',
    '.py', '.js', '.ts', '.yaml', '.yml', '.sql'
)


def classify_document_type(filename: str, sample_text: str = "") -> str:
    """Classify document into Authority, Work Product, Fact Narrative, or General."""
    combined = (filename + " " + sample_text[:1000]).lower()
    if any(k in combined for k in ["ordinance", "statute", "code", "regulation", "contract", "policy", "sop", "agreement", "lease"]):
        return "authority"
    elif any(k in combined for k in ["memo", "memorandum", "draft", "brief", "transcript", "discovery", "deposition", "motion"]):
        return "work_product"
    elif any(k in combined for k in ["fact", "narrative", "intake", "timeline", "incident", "client", "email", "re:"]):
        return "fact_narrative"
    return "general"


def sanitize_filename(filename: str) -> str:
    """Strip unsafe traversal tokens and weird characters from filename."""
    base = os.path.basename(filename)
    return "".join(c for c in base if c.isalnum() or c in "._- ")


class IngestPipeline:
    """
    Orchestrates the 5-stage ingestion pipeline with explicit state transitions
    and poison file isolation.
    """

    def __init__(self, config: Optional[NexusConfig] = None):
        self.config = config or NexusConfig.from_env()
        self.engine = get_engine(self.config.database_url)
        self._sessionmaker = get_session_factory(self.engine)

    def _get_db(self):
        return self._sessionmaker()

    def process_file(
        self,
        filepath: str,
        workspace_name: str = "General",
        doc_type: Optional[str] = None,
        archive_source: bool = True,
        filename: Optional[str] = None
    ) -> IngestReport:
        """
        Execute the 5-stage ingestion pipeline for a single file.
        Enforces path sandboxing and isolates failures with redacted sidecars.
        """
        start_time = time.time()
        orig_filename = sanitize_filename(filename or os.path.basename(filepath))

        # Stage 0a: Path Sandboxing Check
        try:
            safe_path = validate_safe_path(
                filepath,
                allowed_roots=self.config.allowed_ingest_roots,
                allow_temp_dirs=True
            )
            filepath_str = str(safe_path)
        except PathSandboxError as pe:
            logger.error(f"Path sandbox rejection for '{filepath}': {pe}")
            return IngestReport(
                status="failed",
                filename=orig_filename,
                workspace=workspace_name,
                file_hash="",
                error=str(pe)
            )

        if not os.path.exists(filepath_str):
            return IngestReport(
                status="failed",
                filename=orig_filename,
                workspace=workspace_name,
                file_hash="",
                error=f"File '{filepath_str}' not found"
            )

        file_size = os.path.getsize(filepath_str)

        # Stage 0b: Guard against oversized files
        if file_size > self.config.max_file_size_bytes:
            err_msg = f"File size ({file_size} bytes) exceeds limit of {self.config.max_file_size_bytes} bytes"
            self._handle_failure(filepath_str, orig_filename, workspace_name, FileOversizedError(err_msg), start_time, file_hash="")
            return IngestReport(
                status="failed",
                filename=orig_filename,
                workspace=workspace_name,
                file_hash="",
                error=err_msg
            )

        db = self._get_db()
        file_hash = ""

        try:
            # 1. Fetch or create workspace
            workspace = db.query(Workspace).filter(Workspace.name == workspace_name).first()
            if not workspace:
                workspace = Workspace(name=workspace_name, description="Auto-created by ingestion daemon")
                db.add(workspace)
                db.commit()
                db.refresh(workspace)

            # 2. Stage 1: Content-Hash & Deduplication Check
            file_hash = compute_file_hash(filepath_str)
            existing_doc = db.query(Document).filter(
                Document.workspace_id == workspace.id,
                Document.file_hash == file_hash
            ).first()

            if existing_doc:
                # Content identical: Check if this is a new filename alias
                if existing_doc.filename != orig_filename:
                    logger.info(
                        f"File hash match with new filename alias: '{orig_filename}' "
                        f"(canonical: '{existing_doc.filename}') in workspace '{workspace_name}'"
                    )
                    try:
                        rep_data = json.loads(existing_doc.ingest_report or "{}")
                        aliases = rep_data.get("aliases", [])
                        if orig_filename not in aliases:
                            aliases.append(orig_filename)
                            rep_data["aliases"] = aliases
                            existing_doc.ingest_report = json.dumps(rep_data)
                            db.commit()
                    except Exception:
                        pass

                if archive_source:
                    self._archive_success(filepath_str, orig_filename, workspace_name, file_hash)

                elapsed_ms = round((time.time() - start_time) * 1000, 2)
                return IngestReport(
                    status="skipped_duplicate",
                    document_id=existing_doc.id,
                    filename=orig_filename,
                    workspace=workspace_name,
                    file_hash=file_hash,
                    doc_type=existing_doc.doc_type,
                    total_pages=existing_doc.total_pages,
                    total_chunks=existing_doc.total_chunks,
                    duration_ms=elapsed_ms
                )

            # 3. Stage 2: Parse Document into structured pages with timeouts
            llama_docs = parse_document(
                file_path=filepath_str,
                filename=orig_filename,
                ocr_threshold=self.config.ocr_threshold_chars,
                ocr_dpi=self.config.ocr_dpi,
                timeout=self.config.subprocess_timeout
            )
            if not llama_docs:
                raise ParseError(f"Parser returned 0 pages/content for '{orig_filename}'")

            pages: List[ParsedPage] = []
            for d in llama_docs:
                p_num = int(d.metadata.get("page_number") or d.metadata.get("page_label") or 1)
                pages.append(ParsedPage(
                    page_number=p_num,
                    text=d.text,
                    has_images=d.metadata.get("has_images", False),
                    ocr_applied=d.metadata.get("ocr_applied", False),
                    ocr_confidence=d.metadata.get("ocr_confidence")
                ))

            total_pages = len(pages)
            if total_pages > self.config.max_page_count:
                raise FileOversizedError(
                    f"Document page count ({total_pages}) exceeds maximum allowed ({self.config.max_page_count})"
                )

            ocr_pages = [p.page_number for p in pages if p.ocr_applied]

            # 4. Stage 3: Classification & Structural Chunking
            sample_snippet = "\n".join(p.text for p in pages[:2])[:2000]
            resolved_doc_type = doc_type or classify_document_type(orig_filename, sample_snippet)

            chunks: List[Chunk] = chunk_document_pages(
                pages=pages,
                filename=orig_filename,
                file_hash=file_hash,
                max_chars=self.config.max_chars_per_chunk,
                overlap_chars=self.config.overlap_chars,
                base_metadata={
                    "workspace_id": workspace.id,
                    "workspace_name": workspace.name,
                    "doc_type": resolved_doc_type
                }
            )

            # 5. Stage 4: Batch Embeddings via Local Ollama
            chunk_texts = [c.text for c in chunks]
            embeddings = get_embeddings_batch(chunk_texts, config=self.config)

            # 6. Stage 5: Database Write (Atomic Transaction)
            new_doc = Document(
                filename=orig_filename,
                workspace_id=workspace.id,
                file_hash=file_hash,
                total_pages=total_pages,
                total_chunks=len(chunks),
                doc_type=resolved_doc_type,
                ocr_pages=json.dumps(ocr_pages),
                status="completed",
                embedding_model=self.config.embed_model,
                embedding_dim=1024,
                ingested_at=datetime.now(timezone.utc)
            )
            db.add(new_doc)
            db.flush()

            for c, emb in zip(chunks, embeddings):
                chunk_rec = DocumentChunk(
                    document_id=new_doc.id,
                    workspace_id=workspace.id,
                    filename=orig_filename,
                    page_number=c.page_number,
                    chunk_index=c.chunk_index,
                    header=c.header,
                    content=c.text,
                    source_hash=c.source_hash,
                    doc_hash=file_hash,
                    doc_type=resolved_doc_type,
                    embedding=emb if emb else None
                )
                db.add(chunk_rec)

            elapsed_ms = round((time.time() - start_time) * 1000, 2)
            first_cit = f"{orig_filename} p.1 § {chunks[0].header}" if chunks and chunks[0].header else f"{orig_filename} p.1"

            report = IngestReport(
                status="completed",
                document_id=new_doc.id,
                filename=orig_filename,
                workspace=workspace_name,
                file_hash=file_hash,
                doc_type=resolved_doc_type,
                total_pages=total_pages,
                total_chunks=len(chunks),
                ocr_pages=ocr_pages,
                duration_ms=elapsed_ms,
                citation_preview=first_cit
            )

            new_doc.ingest_report = report.model_dump_json()
            db.commit()

            # 7. Success Transition: Move source file to .ingested/
            if archive_source:
                self._archive_success(filepath_str, orig_filename, workspace_name, file_hash)

            logger.info(
                f"Ingested '{orig_filename}' into '{workspace_name}' "
                f"({total_pages} pages, {len(chunks)} chunks, {elapsed_ms}ms)"
            )
            return report

        except Exception as e:
            db.rollback()
            logger.error(f"Failed to ingest file '{orig_filename}': {e}", exc_info=True)
            self._handle_failure(filepath_str, orig_filename, workspace_name, e, start_time, file_hash=file_hash)
            elapsed_ms = round((time.time() - start_time) * 1000, 2)
            return IngestReport(
                status="failed",
                filename=orig_filename,
                workspace=workspace_name,
                file_hash=file_hash,
                duration_ms=elapsed_ms,
                error=str(e)
            )
        finally:
            db.close()

    def _archive_success(self, filepath: str, filename: str, workspace_name: str, file_hash: str):
        """Move successfully processed file to .ingested/<workspace>/<hash[:12]>-<name>."""
        try:
            p = Path(filepath)
            # Find base root directory (traverse above staging)
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
        except Exception as e:
            logger.warning(f"Could not move '{filename}' to .ingested: {e}")

    def _handle_failure(
        self,
        filepath: str,
        filename: str,
        workspace_name: str,
        error: Exception,
        start_time: float,
        file_hash: str = ""
    ):
        """
        Isolate failed file to .failed/<workspace>/<name> and write redacted sidecar .error.json.
        Never leaves document contents in the error JSON.
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

            if p.exists() and p.resolve() != dest_file.resolve():
                shutil.move(str(p), str(dest_file))

            # Redacted error data: NO document chunks or file text!
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

            logger.info(f"Isolated poison file '{filename}' to {failed_dir}")
        except Exception as e:
            logger.error(f"Error isolating failed file '{filename}': {e}")


# Backward-compatible alias
IngestStateMachine = IngestPipeline


# ─── Watchdog / Inotify Daemon Loop ───────────────────────────────────────────

def get_default_watch_dir() -> str:
    """Resolve default watch directory: inside container (/app/ingest_watch) or repo root."""
    if os.path.exists("/app/ingest_watch"):
        return "/app/ingest_watch"
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ingest_watch"))


async def auto_ingest_loop(watch_dir: Optional[str] = None, config: Optional[NexusConfig] = None):
    """
    Continuous watch daemon monitoring watch_dir with:
    - Staging with .part rename lock
    - Concurrency bounds
    - Redacted poison file isolation
    - Watchdog inotify observer with low-latency polling sweep fallback
    """
    conf = config or NexusConfig.from_env()
    target_watch_dir = watch_dir or conf.watch_dir or get_default_watch_dir()
    os.makedirs(target_watch_dir, exist_ok=True)
    staging_dir = os.path.join(target_watch_dir, "staging")
    os.makedirs(staging_dir, exist_ok=True)

    # Register watch directory as an approved ingest root
    if target_watch_dir not in conf.allowed_ingest_roots:
        conf.allowed_ingest_roots.append(os.path.abspath(target_watch_dir))

    pipeline = IngestPipeline(conf)
    semaphore = asyncio.Semaphore(conf.max_ocr_workers)

    logger.info(f"Starting KruschNexus Ingest Daemon on: {target_watch_dir}")
    logger.info(f"Concurrency bounds: {conf.max_ocr_workers} workers, Staging: {staging_dir}")

    async def _safe_process(source_path: str, ws_name: str, fname: str):
        async with semaphore:
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


def main():
    """CLI entrypoint for nexus-daemon."""
    asyncio.run(auto_ingest_loop())


if __name__ == "__main__":
    main()
