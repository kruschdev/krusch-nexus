"""
KruschNexus Public Client SDK
==============================
Typed, versioned Python client library for KruschNexus.
Returns Pydantic models (IngestReport, ChunkHit, Citation, WorkspaceInfo, DocumentInfo).
Does NOT mutate ambient os.environ.
"""

import os
import json
import tempfile
from typing import Optional, List, Dict, Any

from .config import NexusConfig
from .models import IngestReport, ChunkHit, WorkspaceInfo, DocumentInfo
from .exceptions import WorkspaceNotFound, WorkspaceRequiredError
from .db import get_engine, get_session_factory, init_db, Workspace, Document, DocumentChunk
from .embeddings import get_embedding
from .search import hybrid_search


class Nexus:
    """
    Public typed SDK for KruschNexus Document Ingestion and Hybrid Corpus Retrieval.

    Example:
        >>> from krusch_nexus import Nexus
        >>> nx = Nexus.from_env()
        >>> report = nx.ingest("/data/lease.pdf", workspace="Matter_Smith", doc_type="authority")
        >>> hits = nx.search("liquidated damages", workspace="Matter_Smith", limit=8)
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
        init_db(self.engine)
        self._sessionmaker = get_session_factory(self.engine)

    @classmethod
    def from_env(cls, **kwargs) -> "Nexus":
        """Instantiate a configured Nexus client from environment variables."""
        cfg = NexusConfig.from_env(**kwargs)
        return cls(config=cfg)

    def _get_db(self):
        return self._sessionmaker()

    def ingest(
        self,
        filepath: str,
        workspace: str,
        doc_type: str = "general",
        archive: bool = False
    ) -> IngestReport:
        """
        Ingest a local document (PDF, DOCX, EML, CSV, HTML, TXT/MD) into a workspace.
        
        Args:
            filepath: Path to document file on disk.
            workspace: Target workspace name (required).
            doc_type: Document classification category.
            archive: If True, moves the source file to .ingested/ on success.
            
        Returns:
            Typed IngestReport model.
        """
        if not workspace:
            raise WorkspaceRequiredError("A workspace name is required to ingest documents.")

        from .ingest import IngestPipeline
        pipeline = IngestPipeline(self.config)
        return pipeline.process_file(
            filepath=filepath,
            workspace_name=workspace,
            doc_type=doc_type,
            archive_source=archive
        )

    # Alias for backward compatibility
    ingest_file = ingest

    def ingest_text(
        self,
        content: str,
        filename: str,
        workspace: str,
        doc_type: str = "general"
    ) -> IngestReport:
        """Ingest raw text or markdown content directly into the corpus."""
        if not workspace:
            raise WorkspaceRequiredError("A workspace name is required to ingest text.")

        ext = os.path.splitext(filename)[1] or ".txt"
        with tempfile.NamedTemporaryFile(mode="w", suffix=ext, delete=False, encoding="utf-8") as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            return self.ingest(
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
        workspace: str,
        doc_type: Optional[str] = None,
        archive: bool = False,
        recursive: bool = False
    ) -> List[IngestReport]:
        """Batch-ingest all supported documents from a local directory."""
        if not workspace:
            raise WorkspaceRequiredError("A workspace name is required to ingest directory.")

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
                    rep = self.ingest(
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
        workspace: str,
        doc_type: Optional[str] = None,
        limit: int = 5
    ) -> List[ChunkHit]:
        """
        Execute hybrid vector (HNSW) + full-text (tsvector) search with exact citations.
        Returns a list of typed ChunkHit models.
        """
        if not workspace:
            raise WorkspaceRequiredError("Search requires an explicit workspace name.")

        db = self._get_db()
        try:
            ws = db.query(Workspace).filter(Workspace.name.ilike(workspace)).first()
            if not ws:
                return []

            return hybrid_search(
                query=query,
                workspace_id=ws.id,
                db=db,
                embed_fn=lambda txt: get_embedding(txt, config=self.config),
                doc_type=doc_type,
                limit=limit
            )
        finally:
            db.close()

    def search_corpus(
        self,
        query: str,
        workspace: str,
        doc_type: Optional[str] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Dictionary interface for MCP tools and serialized callers."""
        hits = self.search(query=query, workspace=workspace, doc_type=doc_type, limit=limit)
        return [h.model_dump() for h in hits]

    def get_ingest_report(self, doc_id_or_hash: str) -> Optional[IngestReport]:
        """Fetch the stored IngestReport for a document by database ID or SHA-256 hash."""
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
            ws_name = ws.name if ws else f"Workspace_{doc.workspace_id}"
            return IngestReport(
                status=doc.status or "completed",
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
                    status=d.status or "completed",
                    embedding_model=d.embedding_model or "bge-large",
                    created_at=str(d.created_at) if d.created_at else None
                )
                for d in docs
            ]
        finally:
            db.close()


# Backward-compatible alias
NexusIngestClient = Nexus
