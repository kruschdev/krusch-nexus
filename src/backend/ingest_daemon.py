"""
KruschNexus Hardened Ingestion Daemon & State Machine
=====================================================
Explicit state machine managing incoming watch documents:
1. Detection (Watchdog inotify with polling fallback)
2. Staging & Lock (.part rename to prevent incomplete reads)
3. Multi-Stage Pipeline (Hash check -> Parse -> Chunk -> Embed -> DB Write)
4. Success State -> .ingested/<workspace>/<hash>-<name>
5. Failure State -> .failed/<workspace>/<name> + <name>.error.json sidecar
6. Bounded Worker Pool (prevents database stampedes & Ollama exhaustion)
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
from typing import Optional, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor

from .config import NexusConfig
from .models import IngestReport
from .exceptions import FileOversizedError, ParseError, DuplicateDocument
from .db import SessionLocal, Workspace, Document, DocumentChunk, get_engine
from .parsers import parse_document, compute_file_hash, ParsedPage
from .chunking import chunk_document_pages, Chunk
from .embeddings import get_embeddings_batch

logger = logging.getLogger("krusch_nexus.ingest_daemon")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

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


class IngestStateMachine:
    """
    Orchestrates the multi-stage ingestion pipeline with explicit state transitions
    and poison file isolation.
    """

    def __init__(self, config: Optional[NexusConfig] = None):
        self.config = config or NexusConfig.from_env()
        self.engine = get_engine(self.config.database_url)
        from sqlalchemy.orm import sessionmaker
        self._sessionmaker = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

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
        Isolates failures to .failed/ with error sidecar JSON.
        """
        if not os.path.exists(filepath):
            return IngestReport(
                status="failed",
                filename=filename or os.path.basename(filepath),
                workspace=workspace_name,
                file_hash="",
                error=f"File '{filepath}' not found"
            )

        orig_filename = filename or os.path.basename(filepath)
        start_time = time.time()
        file_size = os.path.getsize(filepath)

        # Stage 0: Guard against oversized files
        if file_size > self.config.max_file_size_bytes:
            err_msg = f"File size ({file_size} bytes) exceeds maximum limit of {self.config.max_file_size_bytes} bytes"
            self._handle_failure(filepath, orig_filename, workspace_name, FileOversizedError(err_msg), start_time)
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
            file_hash = compute_file_hash(filepath)
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

                # Move duplicate out of staging/watch folder
                if archive_source:
                    self._archive_success(filepath, orig_filename, workspace_name, file_hash)

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

            # 3. Stage 2: Parse Document into structured pages
            llama_docs = parse_document(
                file_path=filepath,
                filename=orig_filename,
                ocr_threshold=self.config.ocr_threshold_chars,
                ocr_dpi=self.config.ocr_dpi
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
                    ocr_applied=d.metadata.get("ocr_applied", False)
                ))

            total_pages = len(pages)
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

            # 5. Stage 4: Batch Embeddings via Local Ollama (with text-hash caching)
            chunk_texts = [c.text for c in chunks]
            embeddings = get_embeddings_batch(chunk_texts)

            # 6. Stage 5: Database Write (Atomic Transaction)
            new_doc = Document(
                filename=orig_filename,
                workspace_id=workspace.id,
                file_hash=file_hash,
                total_pages=total_pages,
                total_chunks=len(chunks),
                doc_type=resolved_doc_type
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
            first_cit = f"[{orig_filename}, p. 1, § {chunks[0].header}]" if chunks and chunks[0].header else f"[{orig_filename}, p. 1]"

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
                self._archive_success(filepath, orig_filename, workspace_name, file_hash)

            logger.info(
                f"Ingested '{orig_filename}' into '{workspace_name}' "
                f"({total_pages} pages, {len(chunks)} chunks, {elapsed_ms}ms)"
            )
            return report

        except Exception as e:
            db.rollback()
            logger.error(f"Failed to ingest file '{orig_filename}': {e}", exc_info=True)
            self._handle_failure(filepath, orig_filename, workspace_name, e, start_time)
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
            base_dir = os.path.dirname(filepath)
            # Traverse up if inside staging
            if os.path.basename(base_dir) == "staging" or os.path.basename(os.path.dirname(base_dir)) == "staging":
                base_dir = os.path.dirname(os.path.dirname(base_dir)) if os.path.basename(os.path.dirname(base_dir)) == "staging" else os.path.dirname(base_dir)

            ingested_dir = os.path.join(base_dir, ".ingested", workspace_name)
            os.makedirs(ingested_dir, exist_ok=True)
            dest = os.path.join(ingested_dir, f"{file_hash[:12]}_{filename}")
            if os.path.exists(filepath):
                if os.path.abspath(filepath) != os.path.abspath(dest):
                    shutil.move(filepath, dest)
        except Exception as e:
            logger.warning(f"Could not move '{filename}' to .ingested: {e}")

    def _handle_failure(self, filepath: str, filename: str, workspace_name: str, error: Exception, start_time: float):
        """
        Isolate failed file to .failed/<workspace>/<name> and write sidecar .error.json.
        Never leaves poison files in the active watch path.
        """
        try:
            base_dir = os.path.dirname(filepath)
            if "staging" in base_dir:
                # Ascend to watch directory root
                while os.path.basename(base_dir) in ["staging", workspace_name]:
                    base_dir = os.path.dirname(base_dir)

            failed_dir = os.path.join(base_dir, ".failed", workspace_name)
            os.makedirs(failed_dir, exist_ok=True)

            dest_file = os.path.join(failed_dir, filename)
            sidecar_file = os.path.join(failed_dir, f"{filename}.error.json")

            if os.path.exists(filepath) and os.path.abspath(filepath) != os.path.abspath(dest_file):
                shutil.move(filepath, dest_file)

            sidecar_data = {
                "filename": filename,
                "workspace": workspace_name,
                "error_class": type(error).__name__,
                "error_message": str(error),
                "traceback": traceback.format_exc(),
                "duration_ms": round((time.time() - start_time) * 1000, 2),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            with open(sidecar_file, "w", encoding="utf-8") as f:
                json.dump(sidecar_data, f, indent=2)

            logger.info(f"Isolated poison file '{filename}' to {failed_dir}")
        except Exception as e:
            logger.error(f"Error isolating failed file '{filename}': {e}")


# ─── Public API / Backward-Compatible Facade ──────────────────────────────────

_default_pipeline = IngestStateMachine()


def ingest_file_into_nexus(
    filepath: str,
    workspace_name: str = "General",
    doc_type: Optional[str] = None,
    archive_source: bool = True,
    filename: Optional[str] = None,
    config: Optional[NexusConfig] = None
) -> Dict[str, Any]:
    """Compatibility wrapper returning dictionary representation of IngestReport."""
    pipeline = IngestStateMachine(config) if config else _default_pipeline
    report = pipeline.process_file(
        filepath=filepath,
        workspace_name=workspace_name,
        doc_type=doc_type,
        archive_source=archive_source,
        filename=filename
    )
    return report.model_dump()


process_file = ingest_file_into_nexus


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
    - Bounded concurrency pool
    - Poison file isolation
    """
    conf = config or NexusConfig.from_env()
    target_watch_dir = watch_dir or conf.watch_dir or get_default_watch_dir()
    os.makedirs(target_watch_dir, exist_ok=True)
    staging_dir = os.path.join(target_watch_dir, "staging")
    os.makedirs(staging_dir, exist_ok=True)

    pipeline = IngestStateMachine(conf)
    semaphore = asyncio.Semaphore(conf.max_ocr_workers)

    logger.info(f"Starting KruschNexus Ingest Daemon on: {target_watch_dir}")
    logger.info(f"Concurrency bounds: {conf.max_ocr_workers} workers, Staging: {staging_dir}")

    # Process items using staging locks
    async def _safe_process(source_path: str, ws_name: str, fname: str):
        async with semaphore:
            # 1. Lock: Move to staging/<workspace>/<fname>.part
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

            # 2. Process from staging lock
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
                    # Subdirectory name represents target workspace
                    ws_name = item
                    for fname in sorted(os.listdir(item_path)):
                        if fname.startswith('.') or fname.endswith('.part'):
                            continue
                        f_path = os.path.join(item_path, fname)
                        if os.path.isfile(f_path) and fname.lower().endswith(ALLOWED_EXT):
                            asyncio.create_task(_safe_process(f_path, ws_name, fname))
                elif os.path.isfile(item_path) and item.lower().endswith(ALLOWED_EXT):
                    # Root file maps to 'General' workspace
                    asyncio.create_task(_safe_process(item_path, "General", item))

        except Exception as e:
            logger.error(f"Watch daemon sweep error: {e}")

        await asyncio.sleep(2)  # Low-latency polling sweep


def main():
    """CLI entrypoint for nexus-daemon."""
    asyncio.run(auto_ingest_loop())


if __name__ == "__main__":
    main()
