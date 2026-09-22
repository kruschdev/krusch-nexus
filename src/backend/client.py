"""
KruschNexus Ingest Client
=========================
Programmatic interface for homelab services (KruschLaw, KruschBiz, CLI tools)
to perform closed-loop document ingestion, OCR extraction, structural chunking,
and hybrid corpus retrieval against PostgreSQL/pgvector without spawning external servers.
"""

import os
import json
from typing import Optional, Dict, Any, List


class NexusIngestClient:
    """
    Direct Python client for KruschNexus Document Ingestion & Corpus Services.
    """

    def __init__(self, database_url: Optional[str] = None):
        if database_url:
            os.environ["DATABASE_URL"] = database_url

    def ingest_file(
        self,
        filepath: str,
        workspace: str = "General",
        doc_type: str = "general",
        archive: bool = False
    ) -> Dict[str, Any]:
        """
        Ingest a local document (PDF with OCR fallback, DOCX, EML, CSV, HTML, TXT/MD).
        
        Args:
            filepath: Path to the document.
            workspace: Target workspace or matter name.
            doc_type: Category ('authority', 'work_product', 'fact_narrative', 'general').
            archive: If True, moves the file to .ingested/ upon completion.
        
        Returns:
            Standard Ingest Report dict.
        """
        from src.backend.ingest_daemon import ingest_file_into_nexus
        return ingest_file_into_nexus(filepath=filepath, workspace_name=workspace, archive_source=archive)

    def ingest_text(
        self,
        content: str,
        filename: str,
        workspace: str = "General",
        doc_type: str = "general"
    ) -> Dict[str, Any]:
        """
        Ingest raw text or markdown content directly into the corpus.
        """
        import tempfile
        ext = os.path.splitext(filename)[1] or ".txt"
        with tempfile.NamedTemporaryFile(mode="w", suffix=ext, delete=False, encoding="utf-8") as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            from src.backend.ingest_daemon import ingest_file_into_nexus
            return ingest_file_into_nexus(filepath=tmp_path, workspace_name=workspace, archive_source=False, filename=filename)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def ingest_directory(
        self,
        dirpath: str,
        workspace: str = "General",
        archive: bool = False,
        recursive: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Batch-ingest all supported documents from a directory.
        """
        from src.backend.ingest_daemon import ingest_file_into_nexus
        supported_exts = {".pdf", ".docx", ".eml", ".html", ".txt", ".md", ".csv", ".json"}
        results = []

        for root, dirs, files in os.walk(dirpath):
            if not recursive and root != dirpath:
                continue
            dirs[:] = [d for d in dirs if not d.startswith(".")]

            for f in sorted(files):
                if f.startswith("."):
                    continue
                ext = os.path.splitext(f)[1].lower()
                if ext in supported_exts:
                    full_p = os.path.join(root, f)
                    rep = ingest_file_into_nexus(full_p, workspace_name=workspace, archive_source=archive)
                    results.append(rep)

        return results

    def get_ingest_report(self, doc_id_or_hash: str) -> Optional[Dict[str, Any]]:
        """
        Fetch the stored Ingest Report for a document by ID or SHA-256 hash.
        """
        from src.backend.db import SessionLocal, Document
        db = SessionLocal()
        try:
            if str(doc_id_or_hash).isdigit():
                doc = db.query(Document).filter(Document.id == int(doc_id_or_hash)).first()
            else:
                doc = db.query(Document).filter(Document.file_hash == str(doc_id_or_hash)).first()

            if doc and doc.ingest_report:
                return json.loads(doc.ingest_report)
            elif doc:
                return {
                    "document_id": doc.id,
                    "filename": doc.filename,
                    "file_hash": doc.file_hash,
                    "pages_in": doc.total_pages,
                    "chunks_out": doc.total_chunks,
                    "doc_type": doc.doc_type,
                    "status": "completed"
                }
            return None
        finally:
            db.close()

    def search_corpus(
        self,
        query: str,
        workspace: Optional[str] = None,
        doc_type: Optional[str] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Execute hybrid vector (HNSW) + full-text (tsvector) search with exact citations.
        """
        from src.backend.db import SessionLocal, Workspace
        from src.backend.rag_engine import retrieve_hybrid_document_chunks

        db = SessionLocal()
        try:
            workspace_id = None
            if workspace:
                ws = db.query(Workspace).filter(Workspace.name.ilike(workspace)).first()
                if ws:
                    workspace_id = ws.id

            chunks = retrieve_hybrid_document_chunks(
                query_str=query,
                workspace_id=workspace_id,
                doc_type=doc_type,
                limit=limit,
                db=db
            )
            return chunks
        finally:
            db.close()

    def list_workspaces(self) -> List[Dict[str, Any]]:
        """List all workspaces in the corpus."""
        from src.backend.db import SessionLocal, Workspace
        db = SessionLocal()
        try:
            workspaces = db.query(Workspace).all()
            return [
                {"id": w.id, "name": w.name, "description": w.description}
                for w in workspaces
            ]
        finally:
            db.close()

    def list_documents(self, workspace: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all documents in a workspace or across all workspaces."""
        from src.backend.db import SessionLocal, Workspace, Document
        db = SessionLocal()
        try:
            query = db.query(Document)
            if workspace:
                ws = db.query(Workspace).filter(Workspace.name.ilike(workspace)).first()
                if ws:
                    query = query.filter(Document.workspace_id == ws.id)
            docs = query.all()
            return [
                {
                    "id": d.id,
                    "filename": d.filename,
                    "workspace_id": d.workspace_id,
                    "file_hash": d.file_hash,
                    "pages": d.total_pages,
                    "chunks": d.total_chunks,
                    "doc_type": d.doc_type,
                    "has_report": bool(d.ingest_report)
                }
                for d in docs
            ]
        finally:
            db.close()
