"""
KruschNexus Public Client SDK (client.py)
=========================================
Typed Python client library for KruschNexus.
Returns frozen Pydantic contracts: IngestReport, SearchHit, Citation, WorkspaceInfo, DocumentInfo.
Does NOT mutate ambient os.environ.
"""

import os
import json
import logging
from typing import Optional, List, Dict, Any

from .config import NexusConfig
from .models import (
    IngestReport,
    SearchHit,
    Citation,
    WorkspaceInfo,
    DocumentInfo,
    DocType
)
from .exceptions import WorkspaceNotFound, WorkspaceRequiredError, ParseError
from .store import (
    get_engine,
    get_session_factory,
    init_db,
    Workspace,
    Document,
    DocumentChunk,
    IngestReportRecord
)
from .embeddings import get_embedding
from .retrieve import retrieve

logger = logging.getLogger("krusch_nexus.client")


class NexusClient:
    """
    Public typed SDK for KruschNexus Document Ingestion and Hybrid Corpus Retrieval.

    Example:
        >>> from krusch_nexus import NexusClient
        >>> client = NexusClient.from_env()
        >>> report = client.ingest("/data/lease.pdf", workspace="Matter_Smith", doc_type=DocType.AUTHORITY)
        >>> hits = client.search("liquidated damages", workspace="Matter_Smith", limit=5)
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
    def from_env(cls, **kwargs) -> "NexusClient":
        """Instantiate a configured NexusClient from environment variables."""
        cfg = NexusConfig.from_env(**kwargs)
        return cls(config=cfg)

    def _get_db(self):
        return self._sessionmaker()

    def ingest(
        self,
        filepath: str,
        workspace: str,
        doc_type: DocType = DocType.GENERAL,
        archive: bool = False
    ) -> IngestReport:
        """
        Ingest a local document (PDF, DOCX, EML, CSV, HTML, TXT/MD) into a workspace.
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

    def reparse(self, document_id: int) -> IngestReport:
        """
        Re-run the parser and chunker for an existing document in the corpus.
        Operators can use this after parser logic upgrades without manual file transfers.
        """
        db = self._get_db()
        try:
            doc = db.query(Document).filter(Document.id == document_id).first()
            if not doc:
                raise ParseError(f"Document ID {document_id} not found.")

            source_path = doc.original_path
            # Check if source file exists at original location
            if not source_path or not os.path.exists(source_path):
                # Check .ingested/
                ws = db.query(Workspace).filter(Workspace.id == doc.workspace_id).first()
                ws_name = ws.name if ws else "General"
                potential = os.path.join(self.config.watch_dir or "./ingest_watch", ".ingested", ws_name, f"{doc.file_hash[:12]}_{doc.filename}")
                if os.path.exists(potential):
                    source_path = potential
                else:
                    raise ParseError(f"Source file for document ID {document_id} not found at '{source_path}'.")

            # Remove old chunks
            db.query(DocumentChunk).filter(DocumentChunk.document_id == document_id).delete()
            db.commit()

            # Re-ingest
            from .ingest import IngestPipeline
            ws_obj = db.query(Workspace).filter(Workspace.id == doc.workspace_id).first()
            ws_name = ws_obj.name if ws_obj else "General"
            pipeline = IngestPipeline(self.config)
            return pipeline.process_file(
                filepath=source_path,
                workspace_name=ws_name,
                doc_type=doc.doc_type,
                archive_source=False,
                filename=doc.filename
            )
        finally:
            db.close()

    def delete_document(self, document_id: int) -> bool:
        """Delete a document and all its chunks from the database."""
        db = self._get_db()
        try:
            doc = db.query(Document).filter(Document.id == document_id).first()
            if not doc:
                return False
            db.delete(doc)
            db.commit()
            return True
        finally:
            db.close()

    def search(
        self,
        query: str,
        workspace: str,
        doc_type: Optional[str] = None,
        limit: int = 5
    ) -> List[SearchHit]:
        """
        Execute hybrid vector + full-text search across a specific workspace.
        """
        if not workspace:
            raise WorkspaceRequiredError("Search requires an explicit workspace name.")

        db = self._get_db()
        try:
            ws = db.query(Workspace).filter(Workspace.name == workspace).first()
            if not ws:
                return []

            embed_fn = lambda q: get_embedding(q, config=self.config)
            return retrieve(
                query=query,
                workspace_id=ws.id,
                db=db,
                embed_fn=embed_fn,
                doc_type=doc_type,
                limit=limit
            )
        finally:
            db.close()

    def list_workspaces(self) -> List[WorkspaceInfo]:
        """List all document workspaces and their indexed document counts."""
        db = self._get_db()
        try:
            workspaces = db.query(Workspace).all()
            results = []
            for ws in workspaces:
                doc_count = db.query(Document).filter(Document.workspace_id == ws.id).count()
                results.append(WorkspaceInfo(
                    id=ws.id,
                    name=ws.name,
                    description=ws.description,
                    document_count=doc_count,
                    created_at=str(ws.created_at) if ws.created_at else None
                ))
            return results
        finally:
            db.close()

    def list_documents(self, workspace: Optional[str] = None) -> List[DocumentInfo]:
        """List documents in a workspace or across all workspaces."""
        db = self._get_db()
        try:
            q = db.query(Document, Workspace.name.label("ws_name")).join(Workspace, Document.workspace_id == Workspace.id)
            if workspace:
                q = q.filter(Workspace.name == workspace)

            rows = q.all()
            results = []
            for doc, ws_name in rows:
                results.append(DocumentInfo(
                    id=doc.id,
                    workspace_id=doc.workspace_id,
                    workspace_name=ws_name,
                    filename=doc.filename,
                    file_hash=doc.file_hash,
                    doc_type=doc.doc_type,
                    total_pages=doc.total_pages,
                    total_chunks=doc.total_chunks,
                    has_report=bool(doc.ingest_report),
                    status=doc.status,
                    embedding_model=doc.embedding_model,
                    created_at=str(doc.created_at) if doc.created_at else None
                ))
            return results
        finally:
            db.close()

    def get_ingest_report(self, doc_id_or_hash: str) -> Optional[IngestReport]:
        """Retrieve stored IngestReport for a document by its database ID or SHA-256 hash."""
        db = self._get_db()
        try:
            doc = None
            if doc_id_or_hash.isdigit():
                doc = db.query(Document).filter(Document.id == int(doc_id_or_hash)).first()
            if not doc:
                doc = db.query(Document).filter(Document.file_hash == doc_id_or_hash).first()

            if doc and doc.ingest_report:
                data = json.loads(doc.ingest_report)
                return IngestReport(**data)
            return None
        finally:
            db.close()


# Backward-compatible alias
Nexus = NexusClient
NexusIngestClient = NexusClient
