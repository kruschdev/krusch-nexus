"""
KruschNexus Workspace Portability (workspace.py)
================================================
Self-contained workspace export and import using standard .tar.gz archives.
Bundles relational metadata, manifests, chunk JSONL, ingest reports,
and content-addressed source documents to ensure zero database lock-in.
"""

import os
import io
import tarfile
import json
import logging
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from .models import NexusConfig
from .exceptions import WorkspaceNotFound, WorkspaceRequiredError, PathSandboxError, NexusError
from .store import Workspace, Document, DocumentChunk, get_engine, get_session_factory

logger = logging.getLogger("krusch_nexus.workspace")


def _is_safe_tar_member(member: tarfile.TarInfo, dest_dir: Path) -> bool:
    """Verify that a tar member does not escape the destination directory (Tar Slip guard)."""
    target = (dest_dir / member.name).resolve()
    try:
        return target == dest_dir or target.is_relative_to(dest_dir)
    except AttributeError:
        try:
            target.relative_to(dest_dir)
            return True
        except ValueError:
            return False


def export_workspace(
    workspace_name: str,
    output_path: Optional[str] = None,
    config: Optional[NexusConfig] = None,
    engine=None
) -> str:
    """
    Export a complete workspace as a standalone .tar.gz archive.
    Archive contents:
      - workspace.json (metadata, counts, timestamps)
      - manifest.json (document index with hashes and provenance)
      - chunks.jsonl (line-delimited chunk records)
      - reports/ (per-document IngestReport JSON files)
      - documents/ (source files when present on disk)
    """
    if not workspace_name or not workspace_name.strip():
        raise WorkspaceRequiredError("A workspace name is required for export.")

    conf = config or NexusConfig.from_env()
    db_engine = engine or get_engine(conf.database_url)
    sessionmaker = get_session_factory(db_engine)
    db = sessionmaker()

    try:
        ws = db.query(Workspace).filter(Workspace.name == workspace_name.strip()).first()
        if not ws:
            raise WorkspaceNotFound(f"Workspace '{workspace_name}' does not exist.")

        docs = db.query(Document).filter(Document.workspace_id == ws.id).all()
        chunks = db.query(DocumentChunk).filter(DocumentChunk.workspace_id == ws.id).order_by(DocumentChunk.document_id, DocumentChunk.chunk_index).all()

        if output_path:
            out_file = Path(output_path).resolve()
            out_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            safe_ws = "".join(c for c in ws.name if c.isalnum() or c in "_-")
            out_file = Path.cwd() / f"{safe_ws}_export_{ts}.tar.gz"

        with tempfile.TemporaryDirectory() as tmp_dir_str:
            staging = Path(tmp_dir_str)
            reports_dir = staging / "reports"
            docs_dir = staging / "documents"
            reports_dir.mkdir(parents=True, exist_ok=True)
            docs_dir.mkdir(parents=True, exist_ok=True)

            # 1. workspace.json
            ws_meta = {
                "schema_version": "1.0",
                "name": ws.name,
                "description": ws.description,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "documents_count": len(docs),
                "chunks_count": len(chunks),
                "embedding_model": docs[0].embedding_model if docs else conf.embed_model,
                "embedding_dim": docs[0].embedding_dim if docs else conf.embedding_dim or 1024
            }
            with open(staging / "workspace.json", "w", encoding="utf-8") as f:
                json.dump(ws_meta, f, indent=2)

            # 2. manifest.json & reports & documents
            manifest_items = []
            watch_dir = Path(conf.watch_dir) if conf.watch_dir else Path("./ingest_watch")
            ingested_dir = watch_dir / ".ingested" / ws.name

            for d in docs:
                m_item = {
                    "id": d.id,
                    "filename": d.filename,
                    "file_hash": d.file_hash,
                    "version": d.version or 1,
                    "doc_type": d.doc_type,
                    "mime": d.mime,
                    "detected_mime": d.detected_mime,
                    "parser_name": d.parser_name,
                    "parser_version": d.parser_version,
                    "total_pages": d.total_pages,
                    "total_chunks": d.total_chunks,
                    "ocr_pages": d.ocr_pages,
                    "ocr_confidence": d.ocr_confidence,
                    "status": d.status,
                    "embedding_model": d.embedding_model,
                    "embedding_dim": d.embedding_dim,
                    "extra": d.extra,
                    "ingested_at": d.ingested_at.isoformat() if d.ingested_at else None
                }
                manifest_items.append(m_item)

                # Report file
                if d.ingest_report:
                    rep_name = f"{d.file_hash[:12]}_{d.filename}.report.json"
                    with open(reports_dir / rep_name, "w", encoding="utf-8") as rf:
                        rf.write(d.ingest_report)

                # Source file
                src_candidate = None
                if d.original_path and os.path.exists(d.original_path):
                    src_candidate = Path(d.original_path)
                else:
                    pot = ingested_dir / f"{d.file_hash[:12]}_{d.filename}"
                    if pot.exists():
                        src_candidate = pot

                if src_candidate and src_candidate.exists():
                    import shutil
                    dest_f = docs_dir / f"{d.file_hash[:12]}_{d.filename}"
                    try:
                        shutil.copy2(str(src_candidate), str(dest_f))
                    except Exception as ce:
                        logger.debug(f"Could not bundle source file for {d.filename}: {ce}")

            with open(staging / "manifest.json", "w", encoding="utf-8") as f:
                json.dump(manifest_items, f, indent=2)

            # 3. chunks.jsonl
            with open(staging / "chunks.jsonl", "w", encoding="utf-8") as cf:
                for c in chunks:
                    c_dict = {
                        "document_id": c.document_id,
                        "filename": c.filename,
                        "chunk_index": c.chunk_index,
                        "page_number": c.page_number,
                        "locator": c.locator,
                        "header": c.header,
                        "heading_path": c.heading_path,
                        "content": c.content,
                        "citation": c.citation,
                        "source_hash": c.source_hash,
                        "doc_hash": c.doc_hash,
                        "doc_type": c.doc_type,
                        "chunker_version": c.chunker_version,
                        "embed_model": c.embed_model,
                        "confidence": c.confidence,
                        "char_start": c.char_start,
                        "char_end": c.char_end,
                        "is_superseded": getattr(c, "is_superseded", False)
                    }
                    cf.write(json.dumps(c_dict) + "\n")

            # Pack tar.gz
            with tarfile.open(str(out_file), "w:gz") as tar:
                for entry in staging.iterdir():
                    tar.add(str(entry), arcname=entry.name)

        logger.info(f"Exported workspace '{ws.name}' to '{out_file}' ({len(docs)} docs, {len(chunks)} chunks)")
        return str(out_file)
    finally:
        db.close()


def import_workspace(
    tarball_path: str,
    target_workspace: Optional[str] = None,
    config: Optional[NexusConfig] = None,
    engine=None
) -> Dict[str, Any]:
    """
    Import a workspace archive (.tar.gz) into the local database and archival store.
    Guards against path traversal (Tar Slip).
    """
    archive = Path(tarball_path).resolve()
    if not archive.exists():
        raise NexusError(f"Workspace archive '{archive}' not found.")

    conf = config or NexusConfig.from_env()
    db_engine = engine or get_engine(conf.database_url)
    sessionmaker = get_session_factory(db_engine)
    db = sessionmaker()

    try:
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            staging = Path(tmp_dir_str)
            with tarfile.open(str(archive), "r:gz") as tar:
                # Security: check all members
                for member in tar.getmembers():
                    if not _is_safe_tar_member(member, staging):
                        raise PathSandboxError(f"Tar Slip attempt detected in member '{member.name}'")
                tar.extractall(path=staging)

            # Read workspace.json
            ws_json_path = staging / "workspace.json"
            if not ws_json_path.exists():
                raise NexusError("Invalid workspace archive: missing workspace.json")
            with open(ws_json_path, "r", encoding="utf-8") as f:
                ws_meta = json.load(f)

            dest_ws_name = target_workspace.strip() if target_workspace else ws_meta.get("name", "Imported")
            if not dest_ws_name:
                dest_ws_name = "Imported"

            # Create or fetch workspace
            ws = db.query(Workspace).filter(Workspace.name == dest_ws_name).first()
            if not ws:
                ws = Workspace(
                    name=dest_ws_name,
                    description=ws_meta.get("description") or f"Imported from {archive.name}"
                )
                db.add(ws)
                db.commit()
                db.refresh(ws)

            # Read manifest.json
            manifest_path = staging / "manifest.json"
            if not manifest_path.exists():
                raise NexusError("Invalid workspace archive: missing manifest.json")
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest_items = json.load(f)

            doc_id_map: Dict[int, int] = {}
            docs_imported = 0

            for m in manifest_items:
                old_id = m.get("id")
                f_hash = m.get("file_hash", "")
                f_name = m.get("filename", "")

                # Check if document already exists with same hash in this workspace
                existing_doc = db.query(Document).filter(
                    Document.workspace_id == ws.id,
                    Document.file_hash == f_hash
                ).first()

                if existing_doc:
                    if old_id:
                        doc_id_map[old_id] = existing_doc.id
                    continue

                new_doc = Document(
                    workspace_id=ws.id,
                    filename=f_name,
                    file_hash=f_hash,
                    version=m.get("version", 1),
                    doc_type=m.get("doc_type", "general"),
                    mime=m.get("mime", "application/octet-stream"),
                    detected_mime=m.get("detected_mime", "application/octet-stream"),
                    parser_name=m.get("parser_name", "default"),
                    parser_version=m.get("parser_version", "1.0"),
                    chunker_version=m.get("chunker_version", "1.0"),
                    total_pages=m.get("total_pages", 1),
                    total_chunks=m.get("total_chunks", 0),
                    ocr_pages=m.get("ocr_pages"),
                    ocr_confidence=m.get("ocr_confidence"),
                    status=m.get("status", "completed"),
                    embedding_model=m.get("embedding_model", conf.embed_model),
                    embedding_dim=m.get("embedding_dim", conf.embedding_dim or 1024),
                    extra=m.get("extra"),
                    ingested_at=datetime.now(timezone.utc)
                )
                db.add(new_doc)
                db.flush()
                if old_id:
                    doc_id_map[old_id] = new_doc.id
                docs_imported += 1

            # Read chunks.jsonl
            chunks_path = staging / "chunks.jsonl"
            chunks_imported = 0
            if chunks_path.exists():
                with open(chunks_path, "r", encoding="utf-8") as cf:
                    for line in cf:
                        line = line.strip()
                        if not line:
                            continue
                        c_dict = json.loads(line)
                        old_doc_id = c_dict.get("document_id")
                        new_doc_id = doc_id_map.get(old_doc_id)
                        if not new_doc_id:
                            continue

                        # Check if chunk already exists
                        src_h = c_dict.get("source_hash", "")
                        chk_exists = db.query(DocumentChunk).filter(
                            DocumentChunk.document_id == new_doc_id,
                            DocumentChunk.source_hash == src_h
                        ).first()
                        if chk_exists:
                            continue

                        chunk_rec = DocumentChunk(
                            document_id=new_doc_id,
                            workspace_id=ws.id,
                            filename=c_dict.get("filename"),
                            chunk_index=c_dict.get("chunk_index", 0),
                            page_number=c_dict.get("page_number"),
                            locator=c_dict.get("locator"),
                            header=c_dict.get("header"),
                            heading_path=c_dict.get("heading_path"),
                            content=c_dict.get("content", ""),
                            citation=c_dict.get("citation"),
                            source_hash=src_h,
                            doc_hash=c_dict.get("doc_hash", ""),
                            doc_type=c_dict.get("doc_type", "general"),
                            chunker_version=c_dict.get("chunker_version", "1.0"),
                            embed_model=c_dict.get("embed_model", conf.embed_model),
                            confidence=c_dict.get("confidence"),
                            char_start=c_dict.get("char_start"),
                            char_end=c_dict.get("char_end"),
                            is_superseded=c_dict.get("is_superseded", False)
                        )
                        db.add(chunk_rec)
                        chunks_imported += 1

            # Copy documents into .ingested directory if watch_dir is configured
            docs_staging = staging / "documents"
            if docs_staging.exists() and conf.watch_dir:
                dest_ingested = Path(conf.watch_dir) / ".ingested" / ws.name
                dest_ingested.mkdir(parents=True, exist_ok=True)
                for f in docs_staging.iterdir():
                    if f.is_file():
                        import shutil
                        dest_f = dest_ingested / f.name
                        if not dest_f.exists():
                            shutil.copy2(str(f), str(dest_f))

            db.commit()
            logger.info(
                f"Imported workspace '{dest_ws_name}' from '{archive.name}' "
                f"({docs_imported} docs, {chunks_imported} chunks)"
            )
            return {
                "status": "success",
                "workspace": dest_ws_name,
                "documents_imported": docs_imported,
                "chunks_imported": chunks_imported,
                "archive_path": str(archive)
            }
    except Exception as e:
        db.rollback()
        logger.error(f"Import failed for archive '{tarball_path}': {e}")
        raise
    finally:
        db.close()
