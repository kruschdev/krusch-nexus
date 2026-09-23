"""
KruschNexus Hardened Ingestion Pipeline (pipeline.py)
=====================================================
Closed-loop state machine with explicit transitions:
  DETECTED → STAGED → HASHED → PARSED → CHUNKED → EMBEDDED → COMMITTED → ARCHIVED
or FAILED with redacted error sidecar.

Guarantees atomic single-transaction DB commits, provenance recording,
content-addressed disk archival, path sandboxing, and poison file isolation.
"""

import os
import time
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from ..models import (
    NexusConfig,
    IngestReport,
    ParserResult,
    DocType,
    IngestState
)
from ..exceptions import (
    PathSandboxError,
    TooLargeError,
    UnsupportedMimeError,
    ParseError,
    ModelDimensionDriftError
)
from ..store import (
    Workspace,
    Document,
    IngestRun,
    get_engine,
    get_session_factory
)
from ..parsers import parse_document, compute_file_hash
from ..chunking import chunk_document_pages, Chunk
from ..embeddings import get_embeddings_batch
from .sandbox import validate_safe_path, sanitize_filename, ALLOWED_EXT
from .archival import archive_success, handle_failure
from .persist import cleanup_uncommitted_chunks, resolve_document_lineage, commit_document

logger = logging.getLogger("krusch_nexus.ingest.pipeline")


class IngestPipeline:
    """
    Executes the hardened 8-state ingestion pipeline for a single document.
    Enforces atomic DB transactions, provenance recording, and poison isolation.
    """

    def __init__(self, config: Optional[NexusConfig] = None, engine=None):
        self.config = config or NexusConfig.from_env()
        self.engine = engine or get_engine(self.config.database_url)
        self._sessionmaker = get_session_factory(self.engine)

    def _get_db(self):
        return self._sessionmaker()

    def process_file(
        self,
        filepath: str,
        workspace_name: str = "General",
        doc_type: DocType = DocType.GENERAL,
        archive_source: bool = True,
        filename: Optional[str] = None,
        simulate_crash_after_state: Optional[IngestState] = None
    ) -> IngestReport:
        """
        Execute the single-file ingestion pipeline.
        State machine: DETECTED → STAGED → HASHED → PARSED → CHUNKED → EMBEDDED → COMMITTED → ARCHIVED
        """
        start_time = time.time()
        orig_filename = sanitize_filename(filename or os.path.basename(filepath))
        file_hash = ""
        current_state = IngestState.DETECTED

        # Stage 0: Security & Validation
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

        # Check empty file (0-byte)
        file_size = os.path.getsize(filepath_str)
        if file_size == 0:
            err = ParseError(f"File '{orig_filename}' is empty (0 bytes).")
            handle_failure(filepath_str, orig_filename, workspace_name, err, start_time, file_hash="", archive=archive_source, engine=self.engine)
            return IngestReport(
                status="failed",
                filename=orig_filename,
                workspace=workspace_name,
                file_hash="",
                error=f"{err.__class__.__name__}: {str(err)}"
            )

        # Extension check
        ext = os.path.splitext(orig_filename)[1].lower()
        if ext not in ALLOWED_EXT:
            err = UnsupportedMimeError(f"Unsupported file format '{ext}' for file '{orig_filename}'")
            handle_failure(filepath_str, orig_filename, workspace_name, err, start_time, file_hash="", archive=archive_source, engine=self.engine)
            return IngestReport(
                status="failed",
                filename=orig_filename,
                workspace=workspace_name,
                file_hash="",
                error=f"{err.__class__.__name__}: {str(err)}"
            )

        # File size check
        if file_size > self.config.max_file_size_bytes:
            err = TooLargeError(f"File size ({file_size}B) exceeds limit of {self.config.max_file_size_bytes}B")
            handle_failure(filepath_str, orig_filename, workspace_name, err, start_time, file_hash="", archive=archive_source, engine=self.engine)
            return IngestReport(
                status="failed",
                filename=orig_filename,
                workspace=workspace_name,
                file_hash="",
                error=f"{err.__class__.__name__}: {str(err)}"
            )

        mtime = os.path.getmtime(filepath_str)
        current_state = IngestState.STAGED
        db = self._get_db()
        run_rec: Optional[IngestRun] = None

        try:
            # 1. Fetch or create Workspace
            workspace = db.query(Workspace).filter(Workspace.name == workspace_name).first()
            if not workspace:
                workspace = Workspace(name=workspace_name, description="Auto-created by ingestion engine")
                db.add(workspace)
                db.commit()
                db.refresh(workspace)

            # 2. Stage: HASHED (Content deduplication check)
            file_hash = compute_file_hash(filepath_str)
            current_state = IngestState.HASHED

            existing_doc = db.query(Document).filter(
                Document.workspace_id == workspace.id,
                Document.file_hash == file_hash
            ).first()

            if existing_doc and existing_doc.status == IngestState.COMMITTED.value:
                if archive_source:
                    archive_success(filepath_str, orig_filename, workspace_name, file_hash)

                elapsed_ms = round((time.time() - start_time) * 1000, 2)
                try:
                    dup_run = IngestRun(
                        document_id=existing_doc.id,
                        workspace_id=workspace.id,
                        workspace=workspace_name,
                        workspace_name=workspace_name,
                        filename=orig_filename,
                        file_hash=file_hash,
                        state=IngestState.COMMITTED.value,
                        started_at=datetime.fromtimestamp(start_time, timezone.utc),
                        completed_at=datetime.now(timezone.utc),
                        duration_ms=elapsed_ms
                    )
                    db.add(dup_run)
                    db.commit()
                except Exception:
                    db.rollback()
                return IngestReport(
                    status="skipped_duplicate",
                    document_id=existing_doc.id,
                    filename=orig_filename,
                    workspace=workspace_name,
                    file_hash=file_hash,
                    doc_type=existing_doc.doc_type,
                    parser_name=existing_doc.parser_name or "default",
                    parser_version=existing_doc.parser_version or "1.0",
                    detected_mime=existing_doc.detected_mime or existing_doc.mime,
                    pages=existing_doc.total_pages,
                    chunks=existing_doc.total_chunks,
                    duration_ms=elapsed_ms
                )

            # Crash resumption hygiene
            cleanup_uncommitted_chunks(db, workspace.id, file_hash)

            # Create persistent IngestRun ledger record
            run_rec = IngestRun(
                workspace_id=workspace.id,
                workspace=workspace_name,
                workspace_name=workspace_name,
                filename=orig_filename,
                file_hash=file_hash,
                state=IngestState.STAGED.value,
                started_at=datetime.fromtimestamp(start_time, timezone.utc),
            )
            db.add(run_rec)
            db.commit()

            # 3. Stage: PARSED (Multi-format parser with page boundaries & OCR)
            current_state = IngestState.PARSED
            run_rec.state = IngestState.PARSED.value
            db.commit()
            if simulate_crash_after_state == IngestState.PARSED:
                raise SystemExit("Worker killed after PARSED")

            parser_result: ParserResult = parse_document(
                file_path=filepath_str,
                filename=orig_filename,
                ocr_threshold=self.config.ocr_threshold_chars,
                ocr_dpi=self.config.ocr_dpi,
                ocr_lang=self.config.ocr_lang,
                timeout=self.config.subprocess_timeout
            )

            total_pages = parser_result.total_pages
            if total_pages > self.config.max_page_count:
                raise TooLargeError(
                    f"Document page count ({total_pages}) exceeds cap of {self.config.max_page_count}"
                )

            # 4. Stage: CHUNKED (Citation-first structure chunking)
            current_state = IngestState.CHUNKED
            chunks: List[Chunk] = chunk_document_pages(
                pages=parser_result.pages,
                filename=orig_filename,
                file_hash=file_hash,
                max_chars=self.config.max_chars_per_chunk,
                overlap_chars=self.config.overlap_chars,
                doc_type=doc_type,
                base_metadata={
                    "workspace_id": workspace.id,
                    "workspace_name": workspace.name,
                },
                chunker_version="1.0",
                embed_model=self.config.embed_model
            )

            if not chunks:
                raise ParseError(f"No usable content chunks could be generated for '{orig_filename}'")

            run_rec.state = IngestState.CHUNKED.value
            db.commit()
            if simulate_crash_after_state == IngestState.CHUNKED:
                raise SystemExit("Worker killed after CHUNKED")

            # Verify workspace embedding dimension homogeneity
            existing_doc = db.query(Document).filter(
                Document.workspace_id == workspace.id,
                Document.status == IngestState.COMMITTED.value
            ).first()
            if existing_doc and existing_doc.embedding_dim and self.config.embedding_dim and existing_doc.embedding_dim != self.config.embedding_dim:
                raise ModelDimensionDriftError(
                    f"Workspace '{workspace_name}' already contains documents indexed with {existing_doc.embedding_dim}d "
                    f"vectors (model '{existing_doc.embedding_model}'). Cannot ingest new documents with {self.config.embedding_dim}d "
                    f"vectors (model '{self.config.embed_model}'). Reindex or reparse the workspace to change models."
                )

            # 5. Stage: EMBEDDED (Vector generation via local Ollama HTTP client in batches)
            current_state = IngestState.EMBEDDED
            run_rec.state = IngestState.EMBEDDED.value
            db.commit()

            chunk_texts = [c.text for c in chunks]  # Embed raw text only!
            embeddings: List[List[float]] = []
            embed_batch_size = self.config.embed_batch_size
            for b_idx in range(0, len(chunk_texts), embed_batch_size):
                b_slice = chunk_texts[b_idx:b_idx + embed_batch_size]
                b_vecs = get_embeddings_batch(b_slice, config=self.config, db=db)
                for vec in b_vecs:
                    if vec and self.config.embedding_dim and len(vec) != self.config.embedding_dim:
                        raise ModelDimensionDriftError(
                            f"Generated chunk vector dimension {len(vec)} mismatches configured "
                            f"dimension {self.config.embedding_dim} for model '{self.config.embed_model}'."
                        )
                embeddings.extend(b_vecs)

            if simulate_crash_after_state == IngestState.EMBEDDED:
                raise SystemExit("Worker killed after EMBEDDED")

            # 6. Stage: COMMITTED (Atomic single-transaction commit with lineage resolution)
            current_state = IngestState.COMMITTED
            doc_version = resolve_document_lineage(db, workspace.id, orig_filename, file_hash)

            new_doc, report = commit_document(
                db=db,
                workspace=workspace,
                orig_filename=orig_filename,
                file_hash=file_hash,
                parser_result=parser_result,
                chunks=chunks,
                embeddings=embeddings,
                filepath_str=filepath_str,
                mtime=mtime,
                doc_type=doc_type,
                config=self.config,
                version=doc_version,
                run_rec=run_rec,
                start_time=start_time
            )

            # 7. Stage: ARCHIVED (Only move file AFTER DB commit has succeeded!)
            current_state = IngestState.ARCHIVED
            if archive_source:
                manifest_data = {
                    "file_hash": file_hash,
                    "original_filename": orig_filename,
                    "version": doc_version,
                    "pages": total_pages,
                    "chunks": len(chunks),
                    "ocr_pages": report.ocr_pages,
                    "ocr_confidence": report.ocr_confidence,
                    "ocr_mean_confidence": report.ocr_mean_confidence,
                    "parser_name": parser_result.parser_name,
                    "parser_version": parser_result.parser_version,
                    "detected_mime": parser_result.detected_mime,
                    "chunker_version": "1.0",
                    "embed_model": self.config.embed_model,
                    "ingested_at": datetime.now(timezone.utc).isoformat()
                }
                archive_success(filepath_str, orig_filename, workspace_name, file_hash, manifest=manifest_data)
                try:
                    run_rec.state = IngestState.ARCHIVED.value
                    db.commit()
                except Exception:
                    pass

            logger.info(
                f"Ingested '{orig_filename}' v{doc_version} into '{workspace_name}' "
                f"({total_pages} pages, {len(chunks)} chunks, {report.duration_ms}ms)"
            )
            return report

        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            err_class = type(e).__name__
            logger.error(f"Ingest failed at state [{current_state.value}] for '{orig_filename}': {err_class}: {e}")

            handle_failure(
                filepath_str,
                orig_filename,
                workspace_name,
                e,
                start_time,
                file_hash=file_hash,
                archive=archive_source,
                run_rec_id=run_rec.id if run_rec else None,
                engine=self.engine
            )
            elapsed_ms = round((time.time() - start_time) * 1000, 2)
            return IngestReport(
                status="failed",
                filename=orig_filename,
                workspace=workspace_name,
                file_hash=file_hash,
                duration_ms=elapsed_ms,
                error=f"{err_class}: {str(e)}"
            )
        finally:
            db.close()
