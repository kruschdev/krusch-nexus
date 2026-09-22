"""
KruschNexus Ingest & Retrieval Client SDK
=========================================
Frozen, versioned Python SDK contract for KruschNexus.
Returns typed Pydantic models (IngestReport, SearchHit, WorkspaceInfo, DocumentInfo).
Uses NexusConfig object to isolate database and Ollama parameters.
"""

import os
import json
import tempfile
from typing import Optional, List, Dict, Any

from .config import NexusConfig
from .models import IngestReport, SearchHit, WorkspaceInfo, DocumentInfo
from .exceptions import WorkspaceNotFound, NexusError
from .db import get_engine, get_session_factory, Workspace, Document, DocumentChunk
from .embeddings import get_embedding
from .rag_engine import hybrid_search


class NexusIngestClient:
    """
    Public typed SDK for KruschNexus Document Ingestion and Hybrid Corpus Retrieval.
    """

    def __init__(
        self,
        config: Optional[NexusConfig] = None,
        database_url: Optional[str] = None
    ):
        if config:
            self.config = config
        else:
            self.config = NexusConfig.from_env()
            if database_url:
                self.config.database_url = database_url

        self.engine = get_engine(self.config.database_url)
        from sqlalchemy.orm import sessionmaker
        self._sessionmaker = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def _get_db(self):
        return self._sessionmaker()

    def ingest_file(
        self,
        filepath: str,
        workspace: str = "General",
        doc_type: str = "general",
        archive: bool = False
    ) -> IngestReport:
        """
        Ingest a local document (PDF with OCR fallback, DOCX, EML, CSV, HTML, TXT/MD).
        
        Args:
            filepath: Absolute or relative path to the document.
            workspace: Target workspace or matter name.
            doc_type: Category ('authority', 'work_product', 'fact_narrative', 'general').
            archive: If True, moves the file to .ingested/ upon completion.

        Returns:
            Typed IngestReport Pydantic model.
        """
        from .ingest_daemon import IngestStateMachine
        pipeline = IngestStateMachine(self.config)
        return pipeline.process_file(
            filepath=filepath,
            workspace_name=workspace,
            doc_type=doc_type,
            archive_source=archive
        )

    def ingest_text(
        self,
        content: str,
        filename: str,
        workspace: str = "General",
        doc_type: str = "general"
    ) -> IngestReport:
        """
        Ingest raw text or markdown content directly into the corpus.
        """
        ext = os.path.splitext(filename)[1] or ".txt"
        with tempfile.NamedTemporaryFile(mode="w", suffix=ext, delete=False, encoding="utf-8") as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            return self.ingest_file(
                filepath=tmp_path,
                workspace=workspace,
                doc_type=doc_type,
                archive=False
            )
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def ingest_directory(
        self,
        dirpath: str,
        workspace: str = "General",
        doc_type: Optional[str] = None,
        archive: bool = False,
        recursive: bool = False
    ) -> List[IngestReport]:
        """
        Batch-ingest all supported documents from a local directory.
        """
        supported_exts = {".pdf", ".docx", ".doc", ".eml", ".msg", ".html", ".txt", ".md", ".csv", ".json"}
        reports: List[IngestReport] = []

        for root, dirs, files in os.walk(dirpath):
            if not recursive and root != dirpath:
                continue
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in [".ingested", ".failed", "staging"]]

            for f in sorted(files):
                if f.startswith(".") or f.endswith(".part"):
                    continue
                ext = os.path.splitext(f)[1].lower()
                if ext in supported_exts:
                    full_p = os.path.join(root, f)
                    rep = self.ingest_file(
                        filepath=full_p,
                        workspace=workspace,
                        doc_type=doc_type or "general",
                        archive=archive
                    )
                    reports.append(rep)

        return reports

    def search(
        self,
        query: str,
        workspace: str = "General",
        doc_type: Optional[str] = None,
        limit: int = 5
    ) -> List[SearchHit]:
        """
        Execute hybrid vector (HNSW) + full-text (tsvector) search with exact citations.
        Returns a list of typed SearchHit models.
        """
        db = self._get_db()
        try:
            ws = db.query(Workspace).filter(Workspace.name.ilike(workspace)).first()
            if not ws:
                return []

            return hybrid_search(
                query=query,
                workspace_id=ws.id,
                db=db,
                embed_fn=get_embedding,
                doc_type=doc_type,
                limit=limit
            )
        finally:
            db.close()

    def search_corpus(
        self,
        query: str,
        workspace: Optional[str] = "General",
        doc_type: Optional[str] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Backward-compatible dict interface for MCP tools and legacy callers."""
        ws_name = workspace or "General"
        hits = self.search(query=query, workspace=ws_name, doc_type=doc_type, limit=limit)
        return [h.model_dump() for h in hits]

    def get_ingest_report(self, doc_id_or_hash: str) -> Optional[IngestReport]:
        """
        Fetch the stored IngestReport for a document by database ID or SHA-256 hash.
        """
        db = self._get_db()
        try:
            if str(doc_id_or_hash).isdigit():
                doc = db.query(Document).filter(Document.id == int(doc_id_or_hash)).first()
            else:
                doc = db.query(Document).filter(Document.file_hash == str(doc_id_or_hash)).first()

            if not doc:
                return None

            if doc.ingest_report:
                try:
                    data = json.loads(doc.ingest_report)
                    return IngestReport(**data)
                except Exception:
                    pass

            ws = db.query(Workspace).filter(Workspace.id == doc.workspace_id).first()
            ws_name = ws.name if ws else "General"
            return IngestReport(
                status="completed",
                document_id=doc.id,
                filename=doc.filename,
                workspace=ws_name,
                file_hash=doc.file_hash,
                doc_type=doc.doc_type,
                total_pages=doc.total_pages,
                total_chunks=doc.total_chunks
            )
        finally:
            db.close()

    def list_workspaces(self) -> List[WorkspaceInfo]:
        """List all workspaces and their document counts."""
        db = self._get_db()
        try:
            workspaces = db.query(Workspace).all()
            results = []
            for w in workspaces:
                doc_count = db.query(Document).filter(Document.workspace_id == w.id).count()
                results.append(WorkspaceInfo(
                    id=w.id,
                    name=w.name,
                    description=w.description,
                    document_count=doc_count,
                    created_at=str(w.created_at) if w.created_at else None
                ))
            return results
        finally:
            db.close()

    def list_documents(self, workspace: Optional[str] = None) -> List[DocumentInfo]:
        """List all documents in a workspace or across all workspaces."""
        db = self._get_db()
        try:
            query = db.query(Document)
            ws_map = {}
            if workspace:
                ws = db.query(Workspace).filter(Workspace.name.ilike(workspace)).first()
                if not ws:
                    return []
                query = query.filter(Document.workspace_id == ws.id)
                ws_map[ws.id] = ws.name
            else:
                for w in db.query(Workspace).all():
                    ws_map[w.id] = w.name

            docs = query.order_by(Document.id.desc()).all()
            return [
                DocumentInfo(
                    id=d.id,
                    workspace_id=d.workspace_id,
                    workspace_name=ws_map.get(d.workspace_id, f"Workspace_{d.workspace_id}"),
                    filename=d.filename,
                    file_hash=d.file_hash,
                    doc_type=d.doc_type,
                    total_pages=d.total_pages,
                    total_chunks=d.total_chunks,
                    has_report=bool(d.ingest_report),
                    created_at=str(d.created_at) if d.created_at else None
                )
                for d in docs
            ]
        finally:
            db.close()
