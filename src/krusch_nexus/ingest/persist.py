"""
KruschNexus Ingestion Persistence & Lineage Layer (persist.py)
==============================================================
Atomic single-transaction database commits, crash resumption cleanup,
and document version lineage management.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple

from ..models import IngestState, IngestReport, DocType
from ..store import Workspace, Document, DocumentChunk, IngestRun

logger = logging.getLogger("krusch_nexus.ingest.persist")


def cleanup_uncommitted_chunks(db, workspace_id: int, file_hash: str):
    """
    Crash resumption hygiene: clean up any orphaned chunks or partial document
    from a prior interrupted ingestion attempt.
    """
    db.query(DocumentChunk).filter(
        DocumentChunk.workspace_id == workspace_id,
        DocumentChunk.doc_hash == file_hash
    ).delete()

    existing_doc = db.query(Document).filter(
        Document.workspace_id == workspace_id,
        Document.file_hash == file_hash
    ).first()
    if existing_doc and existing_doc.status != IngestState.COMMITTED.value:
        db.delete(existing_doc)
    db.commit()


def resolve_document_lineage(db, workspace_id: int, filename: str, file_hash: str) -> int:
    """
    Check for prior versions of a document with the same filename in this workspace.
    If a prior version exists with a different file_hash:
      - Mark prior Document record as status = 'superseded'
      - Mark prior DocumentChunks as is_superseded = True
      - Return incremented version number
    If no prior version exists, return 1.
    """
    prior_docs = db.query(Document).filter(
        Document.workspace_id == workspace_id,
        Document.filename == filename,
        Document.status == IngestState.COMMITTED.value
    ).order_by(Document.version.desc(), Document.id.desc()).all()

    if not prior_docs:
        return 1

    max_version = 1
    for p_doc in prior_docs:
        if p_doc.file_hash != file_hash:
            p_doc.status = "superseded"
            db.query(DocumentChunk).filter(DocumentChunk.document_id == p_doc.id).update(
                {"is_superseded": True},
                synchronize_session=False
            )
            v = p_doc.version or 1
            if v >= max_version:
                max_version = v + 1

    db.flush()
    return max_version


def commit_document(
    db,
    workspace: Workspace,
    orig_filename: str,
    file_hash: str,
    parser_result: Any,
    chunks: List[Any],
    embeddings: List[List[float]],
    filepath_str: str,
    mtime: float,
    doc_type: DocType,
    config: Any,
    version: int = 1,
    run_rec: Optional[IngestRun] = None,
    start_time: float = 0.0
) -> Tuple[Document, IngestReport]:
    """
    Execute single atomic transaction committing Document, DocumentChunks,
    and updating the IngestRun ledger.
    """
    total_pages = parser_result.total_pages
    ocr_pages = [p.index for p in parser_result.pages if p.ocr_applied and p.index is not None]
    ocr_conf_dict: Dict[int, float] = {
        p.index: p.confidence for p in parser_result.pages
        if p.ocr_applied and p.index is not None and p.confidence is not None
    }
    mean_ocr_conf = (sum(ocr_conf_dict.values()) / len(ocr_conf_dict)) if ocr_conf_dict else None
    resolved_doc_type = doc_type.value if isinstance(doc_type, DocType) else str(doc_type)

    existing_doc = db.query(Document).filter(
        Document.workspace_id == workspace.id,
        Document.file_hash == file_hash
    ).first()

    if existing_doc:
        db.query(DocumentChunk).filter(DocumentChunk.document_id == existing_doc.id).delete(synchronize_session=False)
        existing_doc.filename = orig_filename
        existing_doc.mime = parser_result.mime
        existing_doc.detected_mime = parser_result.detected_mime
        existing_doc.parser_name = parser_result.parser_name
        existing_doc.parser_version = parser_result.parser_version
        existing_doc.chunker_version = "1.0"
        existing_doc.total_pages = total_pages
        existing_doc.total_chunks = len(chunks)
        existing_doc.version = version
        existing_doc.doc_type = resolved_doc_type
        existing_doc.ocr_pages = json.dumps(ocr_pages)
        existing_doc.ocr_confidence = json.dumps(ocr_conf_dict)
        existing_doc.status = IngestState.COMMITTED.value
        existing_doc.original_path = filepath_str
        existing_doc.mtime = mtime
        existing_doc.embedding_model = config.embed_model
        existing_doc.embedding_dim = len(embeddings[0]) if (embeddings and embeddings[0]) else 1024
        existing_doc.extra = json.dumps({"tool_versions": getattr(parser_result, "tool_versions", {})})
        existing_doc.ingested_at = datetime.now(timezone.utc)
        new_doc = existing_doc
        db.flush()
    else:
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
            version=version,
            doc_type=resolved_doc_type,
            ocr_pages=json.dumps(ocr_pages),
            ocr_confidence=json.dumps(ocr_conf_dict),
            status=IngestState.COMMITTED.value,
            original_path=filepath_str,
            mtime=mtime,
            embedding_model=config.embed_model,
            embedding_dim=len(embeddings[0]) if (embeddings and embeddings[0]) else 1024,
            extra=json.dumps({"tool_versions": getattr(parser_result, "tool_versions", {})}),
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
            heading_path=json.dumps(getattr(c, "heading_path", [])),
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
            bbox=json.dumps(getattr(c, "bbox", None)) if getattr(c, "bbox", None) else None,
            is_superseded=False,
            embedding=emb if emb else None
        )
        db.add(chunk_rec)

    import time
    elapsed_ms = round((time.time() - start_time) * 1000, 2)
    first_cit = chunks[0].citation if chunks else orig_filename

    report = IngestReport(
        status="completed",
        document_id=new_doc.id,
        filename=orig_filename,
        workspace=workspace.name,
        file_hash=file_hash,
        doc_type=resolved_doc_type,
        parser_name=parser_result.parser_name,
        parser_version=parser_result.parser_version,
        tool_versions=getattr(parser_result, "tool_versions", {}),
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

    if run_rec:
        run_rec.document_id = new_doc.id
        run_rec.state = IngestState.COMMITTED.value
        run_rec.completed_at = datetime.now(timezone.utc)
        run_rec.duration_ms = elapsed_ms
        run_rec.payload = report.model_dump_json()
    else:
        run_rec = IngestRun(
            document_id=new_doc.id,
            workspace_id=workspace.id,
            workspace=workspace.name,
            workspace_name=workspace.name,
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
    db.commit()
    return new_doc, report
