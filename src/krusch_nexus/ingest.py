"""
KruschNexus Hardened Single-File Ingestion Pipeline (ingest.py)
==============================================================
Closed-loop state machine with explicit transitions:
  DETECTED → STAGED → HASHED → PARSED → CHUNKED → EMBEDDED → COMMITTED → ARCHIVED
or FAILED with redacted error sidecar.

Guarantees atomic single-transaction DB commits, provenance recording,
content-addressed disk archival, path sandboxing, and poison file isolation.
"""

import os
import sys
import time
import json
import shutil
import logging
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from .models import (
    NexusConfig,
    IngestReport,
    PageData,
    ParserResult,
    DocType,
    IngestState,
    WarningCode
)
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
    IngestRun,
    get_engine,
    get_session_factory,
    get_db_session
)
from .parsers import parse_document, compute_file_hash, detect_file_mime
from .chunking import chunk_document_pages, Chunk
from .embeddings import get_embeddings_batch

logger = logging.getLogger("krusch_nexus.ingest")

ALLOWED_EXT = (
    '.pdf', '.docx', '.doc', '.eml', '.msg', '.txt',
    '.md', '.csv', '.json', '.html', '.htm',
    '.py', '.js', '.ts', '.yaml', '.yml', '.sql'
)

# Standard system locations strictly forbidden from ingestion
DENIED_SYSTEM_ROOTS = [
    Path("/etc").resolve(),
    Path("/proc").resolve(),
    Path("/sys").resolve(),
    Path("/dev").resolve(),
    Path("/var/run").resolve(),
    Path(os.path.expanduser("~/.ssh")).resolve(),
    Path(os.path.expanduser("~/.gnupg")).resolve(),
]


def validate_safe_path(
    file_path: str,
    allowed_roots: Optional[List[str]] = None,
    allow_temp_dirs: bool = True
) -> Path:
    """
    Resolve and validate that a target file path is within safe ingest bounds.
    Guards against path traversal, symlink escapes, and system file ingestion.
    """
    try:
        resolved = Path(file_path).resolve()
    except Exception as e:
        raise PathSandboxError(f"Invalid path specification '{file_path}': {e}")

    # 1. Deny forbidden system paths
    for denied in DENIED_SYSTEM_ROOTS:
        try:
            if resolved == denied or resolved.is_relative_to(denied):
                raise PathSandboxError(
                    f"Access denied: Path '{resolved}' falls within prohibited system directory '{denied}'"
                )
        except AttributeError:
            try:
                resolved.relative_to(denied)
                raise PathSandboxError(
                    f"Access denied: Path '{resolved}' falls within prohibited system directory '{denied}'"
                )
            except ValueError:
                pass

    # 2. Enforce allowed roots
    valid_roots: List[Path] = []
    if allowed_roots:
        for r in allowed_roots:
            if r:
                valid_roots.append(Path(r).resolve())
    else:
        valid_roots.append(Path.cwd().resolve())

    if allow_temp_dirs:
        valid_roots.append(Path(tempfile.gettempdir()).resolve())
        if os.path.exists("/tmp"):
            valid_roots.append(Path("/tmp").resolve())

    if valid_roots:
        is_safe = False
        for root in valid_roots:
            try:
                if resolved == root or resolved.is_relative_to(root):
                    is_safe = True
                    break
            except AttributeError:
                try:
                    resolved.relative_to(root)
                    is_safe = True
                    break
                except ValueError:
                    pass

        if not is_safe:
            roots_str = ", ".join(str(r) for r in valid_roots)
            raise PathSandboxError(
                f"Path sandbox violation: '{resolved}' is not within any approved ingest root: [{roots_str}]"
            )

    return resolved


def reap_stale_locks(
    staging_dir: str,
    timeout_seconds: float = 600.0,
    max_age_seconds: Optional[float] = None
) -> List[str]:
    """
    Scan staging directory for abandoned .part and .lock files older than timeout.
    Quarantines or unlinks them to prevent deadlocks.
    Returns list of reaped file paths.
    """
    timeout = max_age_seconds if max_age_seconds is not None else timeout_seconds
    reaped = []
    now = time.time()
    if not os.path.exists(staging_dir):
        return reaped

    for root, _, files in os.walk(staging_dir):
        for f in files:
            if f.endswith(".part") or f.endswith(".lock"):
                f_path = os.path.join(root, f)
                try:
                    mtime = os.path.getmtime(f_path)
                    if now - mtime > timeout:
                        if f.endswith(".lock"):
                            try:
                                os.unlink(f_path)
                            except OSError:
                                pass
                            reaped.append(f_path)
                            logger.info(f"Reaped stale lock file: {f_path}")
                        else:
                            ws_name = os.path.basename(root)
                            base_dir = Path(staging_dir).parent
                            failed_dir = base_dir / ".failed" / ws_name
                            failed_dir.mkdir(parents=True, exist_ok=True)
                            clean_name = f.replace(".part", "")
                            dest = failed_dir / f"stale_{clean_name}"
                            shutil.move(f_path, str(dest))
                            sidecar = failed_dir / f"stale_{clean_name}.error.json"
                            with open(sidecar, "w", encoding="utf-8") as s_file:
                                json.dump({
                                    "filename": clean_name,
                                    "workspace": ws_name,
                                    "error_class": "StaleLockReaped",
                                    "error_message": f"Lock age ({int(now - mtime)}s) exceeded timeout ({int(timeout)}s)",
                                    "timestamp": datetime.now(timezone.utc).isoformat()
                                }, s_file, indent=2)
                            reaped.append(str(dest))
                            logger.warning(f"Reaped stale lock: {f_path} -> {dest}")
                except Exception as e:
                    logger.debug(f"Error checking potential stale lock {f_path}: {e}")
    return reaped


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

        # Check empty file (0-byte)
        file_size = os.path.getsize(filepath_str)
        if file_size == 0:
            err = ParseError(f"File '{orig_filename}' is empty (0 bytes).")
            self._handle_failure(filepath_str, orig_filename, workspace_name, err, start_time, file_hash="")
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
            self._handle_failure(filepath_str, orig_filename, workspace_name, err, start_time, file_hash="")
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
            self._handle_failure(filepath_str, orig_filename, workspace_name, err, start_time, file_hash="")
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

            # 3. Stage: PARSED (Multi-format parser with page boundaries & OCR)
            current_state = IngestState.PARSED
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

            ocr_pages = [p.index for p in parser_result.pages if p.ocr_applied and p.index is not None]
            ocr_conf_dict: Dict[int, float] = {
                p.index: p.confidence for p in parser_result.pages
                if p.ocr_applied and p.index is not None and p.confidence is not None
            }
            mean_ocr_conf = (sum(ocr_conf_dict.values()) / len(ocr_conf_dict)) if ocr_conf_dict else None

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

            # 5. Stage: EMBEDDED (Vector generation via local Ollama HTTP client in batches)
            current_state = IngestState.EMBEDDED
            chunk_texts = [c.text for c in chunks]  # Embed raw text only!
            embeddings: List[List[float]] = []
            embed_batch_size = self.config.embed_batch_size
            for b_idx in range(0, len(chunk_texts), embed_batch_size):
                b_slice = chunk_texts[b_idx:b_idx + embed_batch_size]
                b_vecs = get_embeddings_batch(b_slice, config=self.config, db=db)
                embeddings.extend(b_vecs)

            # 6. Stage: COMMITTED (Atomic single-transaction commit)
            current_state = IngestState.COMMITTED
            resolved_doc_type = doc_type.value if isinstance(doc_type, DocType) else str(doc_type)

            new_doc = Document(
                filename=orig_filename,
                workspace_id=workspace.id,
                file_hash=file_hash,
                mime=parser_result.mime,
                detected_mime=parser_result.detected_mime,
                parser_name=parser_result.parser_name,
                parser_version=parser_result.parser_version,
                chunker_version="1.0",
                total_pages=total_pages,
                total_chunks=len(chunks),
                doc_type=resolved_doc_type,
                ocr_pages=json.dumps(ocr_pages),
                ocr_confidence=json.dumps(ocr_conf_dict),
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
                parser_name=parser_result.parser_name,
                parser_version=parser_result.parser_version,
                detected_mime=parser_result.detected_mime,
                pages=total_pages,
                chunks=len(chunks),
                ocr_pages=ocr_pages,
                ocr_confidence=ocr_conf_dict,
                ocr_mean_confidence=mean_ocr_conf,
                duration_ms=elapsed_ms,
                warnings=parser_result.warnings,
                citation_preview=first_cit
            )

            # Record run in ingest_runs ledger
            run_rec = IngestRun(
                document_id=new_doc.id,
                workspace_id=workspace.id,
                workspace=workspace_name,
                workspace_name=workspace_name,
                filename=orig_filename,
                file_hash=file_hash,
                state=IngestState.COMMITTED.value,
                started_at=datetime.fromtimestamp(start_time, timezone.utc),
                completed_at=datetime.now(timezone.utc),
                duration_ms=elapsed_ms,
                payload=report.model_dump_json()
            )
            db.add(run_rec)
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
                    "ocr_confidence": ocr_conf_dict,
                    "ocr_mean_confidence": mean_ocr_conf,
                    "parser_name": parser_result.parser_name,
                    "parser_version": parser_result.parser_version,
                    "detected_mime": parser_result.detected_mime,
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

            # Record failure run in ingest_runs ledger
            try:
                fail_run = IngestRun(
                    filename=orig_filename,
                    workspace=workspace_name,
                    workspace_name=workspace_name,
                    file_hash=file_hash,
                    state=IngestState.FAILED.value,
                    started_at=datetime.fromtimestamp(start_time, timezone.utc),
                    completed_at=datetime.now(timezone.utc),
                    duration_ms=round((time.time() - start_time) * 1000, 2),
                    error_class=err_class,
                    error_message=str(e)
                )
                db.add(fail_run)
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

            # Persist failure to IngestRun ledger
            try:
                with get_db_session(self.engine) as db_sess:
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
