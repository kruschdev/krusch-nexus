"""
KruschNexus Hardened Single-File Ingestion Pipeline (ingest.py)
==============================================================
Closed-loop state machine with explicit transitions:
  DETECTED → STAGED → HASHED → PARSED → CHUNKED → EMBEDDED → COMMITTED → ARCHIVED
or FAILED with redacted error sidecar.

Guarantees atomic single-transaction DB commits, provenance recording,
and poison file isolation.
"""

import os
import sys
import time
import json
import shutil
import logging
from enum import Enum
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from .config import NexusConfig
from .models import IngestReport, PageData, ParserResult, DocType
from .sandbox import validate_safe_path
from .exceptions import (
    PathSandboxError,
    FileOversizedError,
    TooLargeError,
    EncryptedPdfError,
    EmptyOcrError,
    UnsupportedMimeError,
    ParseError
)
from .store import (
    Workspace,
    Document,
    DocumentChunk,
    IngestReportRecord,
    get_engine,
    get_session_factory
)
from .parsers import parse_document, compute_file_hash
from .chunking import chunk_document_pages, Chunk
from .embeddings import get_embeddings_batch

logger = logging.getLogger("krusch_nexus.ingest")

ALLOWED_EXT = (
    '.pdf', '.docx', '.doc', '.eml', '.msg', '.txt',
    '.md', '.csv', '.json', '.html', '.htm',
    '.py', '.js', '.ts', '.yaml', '.yml', '.sql'
)


class IngestState(str, Enum):
    """Explicit lifecycle states for document ingestion."""
    DETECTED = "detected"
    STAGED = "staged"
    HASHED = "hashed"
    PARSED = "parsed"
    CHUNKED = "chunked"
    EMBEDDED = "embedded"
    COMMITTED = "committed"
    ARCHIVED = "archived"
    FAILED = "failed"


def sanitize_filename(filename: str) -> str:
    """Strip traversal tokens and hazardous characters from filename."""
    base = os.path.basename(filename)
    return "".join(c for c in base if c.isalnum() or c in "._- ")


class IngestPipeline:
    """
    Executes the hardened 8-state ingestion pipeline for a single document.
    Enforces atomic DB transactions, provenance recording, and poison isolation.
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
        doc_type: DocType = DocType.GENERAL,
        archive_source: bool = True,
        filename: Optional[str] = None
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

        # Extension check
        ext = os.path.splitext(orig_filename)[1].lower()
        if ext not in ALLOWED_EXT:
            err = UnsupportedMimeError(f"Unsupported file format '{ext}' for file '{orig_filename}'")
            self._handle_failure(filepath_str, orig_filename, workspace_name, err, start_time, file_hash="")
            return IngestReport(
                status="failed",
                filename=orig_filename,
                workspace=workspace_name,
                file_hash="",
                error=str(err)
            )

        # File size check
        file_size = os.path.getsize(filepath_str)
        if file_size > self.config.max_file_size_bytes:
            err = TooLargeError(f"File size ({file_size}B) exceeds limit of {self.config.max_file_size_bytes}B")
            self._handle_failure(filepath_str, orig_filename, workspace_name, err, start_time, file_hash="")
            return IngestReport(
                status="failed",
                filename=orig_filename,
                workspace=workspace_name,
                file_hash="",
                error=str(err)
            )

        mtime = os.path.getmtime(filepath_str)
        current_state = IngestState.STAGED
        db = self._get_db()

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

            if existing_doc:
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
                    pages=existing_doc.total_pages,
                    chunks=existing_doc.total_chunks,
                    duration_ms=elapsed_ms
                )

            # 3. Stage: PARSED (Multi-format parser with page boundaries & OCR)
            current_state = IngestState.PARSED
            parser_result: ParserResult = parse_document(
                file_path=filepath_str,
                filename=orig_filename,
                ocr_threshold=self.config.ocr_threshold_chars,
                ocr_dpi=self.config.ocr_dpi,
                timeout=self.config.subprocess_timeout
            )

            total_pages = parser_result.total_pages
            if total_pages > self.config.max_page_count:
                raise TooLargeError(
                    f"Document page count ({total_pages}) exceeds cap of {self.config.max_page_count}"
                )

            ocr_pages = [p.index for p in parser_result.pages if p.ocr_applied and p.index is not None]
            ocr_confs = [p.confidence for p in parser_result.pages if p.ocr_applied and p.confidence is not None]
            mean_ocr_conf = (sum(ocr_confs) / len(ocr_confs)) if ocr_confs else None

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

            # 5. Stage: EMBEDDED (Vector generation via local Ollama HTTP client in batches of 16)
            current_state = IngestState.EMBEDDED
            chunk_texts = [c.text for c in chunks]  # Embed raw text only!
            embeddings: List[List[float]] = []
            embed_batch_size = 16
            for b_idx in range(0, len(chunk_texts), embed_batch_size):
                b_slice = chunk_texts[b_idx:b_idx + embed_batch_size]
                b_vecs = get_embeddings_batch(b_slice, config=self.config)
                embeddings.extend(b_vecs)

            # 6. Stage: COMMITTED (Atomic single-transaction commit)
            current_state = IngestState.COMMITTED
            resolved_doc_type = doc_type.value if isinstance(doc_type, DocType) else str(doc_type)

            new_doc = Document(
                filename=orig_filename,
                workspace_id=workspace.id,
                file_hash=file_hash,
                mime=parser_result.mime,
                parser_version=parser_result.parser_version,
                chunker_version="1.0",
                total_pages=total_pages,
                total_chunks=len(chunks),
                doc_type=resolved_doc_type,
                ocr_pages=json.dumps(ocr_pages),
                status=IngestState.COMMITTED.value,
                original_path=filepath_str,
                mtime=mtime,
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
                    chunk_index=c.chunk_index,
                    page_number=c.page_number,
                    locator=c.locator,
                    header=c.header,
                    content=c.text,
                    citation=c.citation,
                    source_hash=c.source_hash,
                    doc_hash=file_hash,
                    doc_type=resolved_doc_type,
                    chunker_version=c.chunker_version,
                    embed_model=c.embed_model,
                    confidence=c.confidence,
                    char_start=c.char_start,
                    char_end=c.char_end,
                    embedding=emb if emb else None
                )
                db.add(chunk_rec)

            elapsed_ms = round((time.time() - start_time) * 1000, 2)
            first_cit = chunks[0].citation if chunks else orig_filename

            report = IngestReport(
                status="completed",
                document_id=new_doc.id,
                filename=orig_filename,
                workspace=workspace_name,
                file_hash=file_hash,
                doc_type=resolved_doc_type,
                pages=total_pages,
                chunks=len(chunks),
                ocr_pages=ocr_pages,
                ocr_mean_confidence=mean_ocr_conf,
                duration_ms=elapsed_ms,
                warnings=parser_result.warnings,
                citation_preview=first_cit
            )

            # Add ledger record to ingest_reports
            report_rec = IngestReportRecord(
                document_id=new_doc.id,
                workspace_id=workspace.id,
                filename=orig_filename,
                file_hash=file_hash,
                status="completed",
                duration_ms=elapsed_ms,
                payload=report.model_dump_json()
            )
            db.add(report_rec)
            new_doc.ingest_report = report.model_dump_json()

            # ATOMIC COMMIT of all records
            db.commit()

            # 7. Stage: ARCHIVED (Only move file AFTER DB commit has succeeded!)
            current_state = IngestState.ARCHIVED
            if archive_source:
                manifest_data = {
                    "file_hash": file_hash,
                    "original_filename": orig_filename,
                    "pages": total_pages,
                    "chunks": len(chunks),
                    "ocr_pages": ocr_pages,
                    "ocr_mean_confidence": mean_ocr_conf,
                    "parser_name": parser_result.parser_name,
                    "parser_version": parser_result.parser_version,
                    "chunker_version": "1.0",
                    "embed_model": self.config.embed_model,
                    "ingested_at": datetime.now(timezone.utc).isoformat()
                }
                self._archive_success(filepath_str, orig_filename, workspace_name, file_hash, manifest=manifest_data)

            logger.info(
                f"Ingested '{orig_filename}' into '{workspace_name}' "
                f"({total_pages} pages, {len(chunks)} chunks, {elapsed_ms}ms)"
            )
            return report

        except Exception as e:
            db.rollback()
            err_class = type(e).__name__
            logger.error(f"Ingest failed at state [{current_state.value}] for '{orig_filename}': {err_class}: {e}")

            # Record failure in ledger if DB available
            try:
                fail_rec = IngestReportRecord(
                    filename=orig_filename,
                    file_hash=file_hash,
                    status="failed",
                    duration_ms=round((time.time() - start_time) * 1000, 2),
                    error_class=err_class,
                    error_message=str(e)
                )
                db.add(fail_rec)
                db.commit()
            except Exception:
                db.rollback()

            self._handle_failure(filepath_str, orig_filename, workspace_name, e, start_time, file_hash=file_hash)
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

    def _archive_success(
        self,
        filepath: str,
        filename: str,
        workspace_name: str,
        file_hash: str,
        manifest: Optional[Dict[str, Any]] = None
    ):
        """Move successfully processed file to .ingested/<workspace>/<hash[:12]>_<name> and write manifest JSON."""
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
        Never leaves document chunks or raw file text in the error JSON.
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

            logger.info(f"Isolated poison file '{filename}' ({type(error).__name__}) to {failed_dir}")
        except Exception as e:
            logger.error(f"Error isolating failed file '{filename}': {e}")
