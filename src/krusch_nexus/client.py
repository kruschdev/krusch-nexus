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
import secrets
from typing import Optional, List, Dict, Any, Union

from .models import (
    NexusConfig,
    IngestReport,
    SearchHit,
    SearchFilter,
    Citation,
    WorkspaceInfo,
    DocumentInfo,
    DocType
)
from .exceptions import WorkspaceNotFound, WorkspaceRequiredError, ParseError, AuthenticationError
from .store import (
    get_engine,
    get_session_factory,
    init_db,
    Workspace,
    Document,
    DocumentChunk,
    IngestRun
)
from .embeddings import get_embedding
from .retrieve import retrieve

logger = logging.getLogger("krusch_nexus.client")


class NexusClient:
    """
    Canonical typed SDK for KruschNexus Document Ingestion and Hybrid Corpus Retrieval.

    Example:
        >>> from krusch_nexus import NexusClient, DocType
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
        self.search_engine = get_engine(self.config.search_database_url or self.config.database_url)
        init_db(self.engine)
        self._sessionmaker = get_session_factory(self.engine)
        self._search_sessionmaker = get_session_factory(self.search_engine)

    @classmethod
    def from_env(cls, **kwargs) -> "NexusClient":
        """Instantiate a configured NexusClient from environment variables."""
        cfg = NexusConfig.from_env(**kwargs)
        return cls(config=cfg)

    def _get_db(self):
        return self._sessionmaker()

    def _get_search_db(self):
        return self._search_sessionmaker()

    def ingest(
        self,
        filepath: str,
        workspace: str,
        doc_type: DocType = DocType.GENERAL,
        archive: bool = False,
        simulate_crash_after_state: Optional[Any] = None
    ) -> IngestReport:
        """
        Ingest a local document (PDF, DOCX, EML, CSV, HTML, TXT/MD) into a workspace.
        """
        if not workspace or not workspace.strip():
            raise WorkspaceRequiredError("A workspace name is required to ingest documents.")

        from .ingest import IngestPipeline
        pipeline = IngestPipeline(self.config, engine=self.engine)
        return pipeline.process_file(
            filepath=filepath,
            workspace_name=workspace.strip(),
            doc_type=doc_type,
            archive_source=archive,
            simulate_crash_after_state=simulate_crash_after_state
        )

    ingest_file = ingest

    def reparse(self, document_id: int, operator_token: Optional[str] = None) -> IngestReport:
        """
        Re-run the parser and chunker for an existing document in the corpus.
        Operators can use this after parser logic upgrades without manual file transfers.
        """
        if self.config.operator_token:
            if not operator_token or not secrets.compare_digest(operator_token, self.config.operator_token):
                raise AuthenticationError("Operator authorization required to execute document reparse.")

        db = self._get_db()
        try:
            doc = db.query(Document).filter(Document.id == document_id).first()
            if not doc:
                raise ParseError(f"Document ID {document_id} not found.")

            source_path = doc.original_path
            if not source_path or not os.path.exists(source_path):
                ws = db.query(Workspace).filter(Workspace.id == doc.workspace_id).first()
                ws_name = ws.name if ws else "General"
                potential = os.path.join(self.config.watch_dir or "./ingest_watch", ".ingested", ws_name, f"{doc.file_hash[:12]}_{doc.filename}")
                if os.path.exists(potential):
                    source_path = potential
                else:
                    raise ParseError(f"Source file for document ID {document_id} not found at '{source_path}'.")

            ws_name = doc.workspace.name if doc.workspace else "General"
            doc_type_val = doc.doc_type
            doc_filename = doc.filename

            # Remove old document and cascaded chunks to force fresh reparse
            db.delete(doc)
            db.commit()

            from .ingest import IngestPipeline
            pipeline = IngestPipeline(self.config, engine=self.engine)
            return pipeline.process_file(
                filepath=source_path,
                workspace_name=ws_name,
                doc_type=doc_type_val,
                archive_source=False,
                filename=doc_filename
            )
        finally:
            db.close()

    reindex = reparse

    def delete_document(self, document_id: int, operator_token: Optional[str] = None) -> bool:
        """
        Delete a document and all its chunks from the database.
        Destructive operator operation.
        """
        if self.config.operator_token:
            if not operator_token or not secrets.compare_digest(operator_token, self.config.operator_token):
                raise AuthenticationError("Operator authorization required to delete corpus documents.")

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

    def get_ingest_report(self, doc_id_or_hash: Union[int, str]) -> Optional[IngestReport]:
        """Fetch the stored IngestReport for a document by ID or SHA-256 hash."""
        db = self._get_db()
        try:
            query = db.query(Document)
            if isinstance(doc_id_or_hash, int) or str(doc_id_or_hash).isdigit():
                doc = query.filter(Document.id == int(doc_id_or_hash)).first()
            else:
                doc = query.filter(Document.file_hash == str(doc_id_or_hash)).first()

            if doc and doc.ingest_report:
                return IngestReport.model_validate_json(doc.ingest_report)
            return None
        finally:
            db.close()

    def search(
        self,
        query: str,
        workspace: str,
        doc_type: Optional[str] = None,
        limit: int = 5,
        filters: Optional[Union[SearchFilter, Dict[str, Any]]] = None
    ) -> List[SearchHit]:
        """
        Execute hybrid vector + full-text search across a workspace.
        Returns ranked SearchHit models with canonical citations and explainability metadata.
        """
        if not workspace or not workspace.strip():
            raise WorkspaceRequiredError("A target workspace is required for search. Global multi-workspace search is disallowed.")

        db = self._get_search_db()
        try:
            ws = db.query(Workspace).filter(Workspace.name == workspace.strip()).first()
            if not ws:
                return []

            def _embed(text_str: str) -> List[float]:
                return get_embedding(text_str, config=self.config, db=db)

            if isinstance(filters, SearchFilter):
                resolved_filters = filters.model_dump(exclude_none=True)
            elif isinstance(filters, dict):
                resolved_filters = dict(filters)
            else:
                resolved_filters = {}

            return retrieve(
                query=query,
                workspace_id=ws.id,
                db=db,
                embed_fn=_embed,
                doc_type=doc_type,
                limit=limit,
                filters=resolved_filters,
                config=self.config
            )
        finally:
            db.close()

    def list_workspaces(self) -> List[WorkspaceInfo]:
        """List all workspaces and their indexed document counts."""
        db = self._get_db()
        try:
            workspaces = db.query(Workspace).all()
            res = []
            for w in workspaces:
                count = db.query(Document).filter(Document.workspace_id == w.id).count()
                res.append(WorkspaceInfo(
                    id=w.id,
                    name=w.name,
                    description=w.description,
                    document_count=count,
                    created_at=w.created_at.isoformat() if w.created_at else None
                ))
            return res
        finally:
            db.close()

    def list_documents(self, workspace: Optional[str] = None) -> List[DocumentInfo]:
        """List documents with workspace isolation."""
        db = self._get_db()
        try:
            query = db.query(Document)
            if workspace and workspace.strip():
                ws = db.query(Workspace).filter(Workspace.name == workspace.strip()).first()
                if not ws:
                    return []
                query = query.filter(Document.workspace_id == ws.id)

            docs = query.all()
            ws_map = {w.id: w.name for w in db.query(Workspace).all()}
            res = []
            for d in docs:
                res.append(DocumentInfo(
                    id=d.id,
                    workspace_id=d.workspace_id,
                    workspace_name=ws_map.get(d.workspace_id, "Unknown"),
                    filename=d.filename,
                    file_hash=d.file_hash,
                    doc_type=d.doc_type,
                    total_pages=d.total_pages,
                    total_chunks=d.total_chunks,
                    parser_name=d.parser_name or "default",
                    parser_version=d.parser_version or "1.0",
                    detected_mime=d.detected_mime or d.mime,
                    has_report=bool(d.ingest_report),
                    status=d.status,
                    embedding_model=d.embedding_model,
                    chunker_version=d.chunker_version,
                    created_at=d.created_at.isoformat() if d.created_at else None
                ))
            return res
        finally:
            db.close()


# Canonical thin alias for backwards-compatibility
Nexus = NexusClient
