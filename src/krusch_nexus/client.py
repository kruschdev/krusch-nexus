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
from typing import Optional, List, Dict, Any, Union, Tuple

from .models import (
    NexusConfig,
    IngestReport,
    SearchHit,
    SearchFilter,
    WorkspaceInfo,
    DocumentInfo,
    DocType
)
from .exceptions import (
    WorkspaceNotFound,
    WorkspaceRequiredError,
    ParseError,
    AuthenticationError,
    LegalHoldActiveError,
    ConfigurationError
)
from .store import (
    get_engine,
    get_session_factory,
    init_db,
    Workspace,
    Document,
    DocumentChunk,
    OperatorAudit
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

    def _get_pipeline(self):
        from .ingest import IngestPipeline
        return IngestPipeline(self.config, engine=self.engine)

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

        pipeline = self._get_pipeline()
        return pipeline.process_file(
            filepath=filepath,
            workspace_name=workspace.strip(),
            doc_type=doc_type,
            archive_source=archive,
            simulate_crash_after_state=simulate_crash_after_state
        )

    ingest_file = ingest

    def parse_and_chunk(
        self,
        filepath: str,
        doc_type: DocType = DocType.GENERAL,
        max_chars: Optional[int] = None,
        overlap_chars: Optional[int] = None,
    ) -> Tuple[Any, List[Dict[str, Any]]]:
        """
        Library mode: Parse and chunk a local file without database or embedding dependencies.
        Returns a tuple of (ParserResult, list of chunk dictionaries with locators).
        """
        from .parsers import parse_document
        from .parsers.registry import compute_file_hash
        from .chunking import chunk_document_pages
        from .ingest.sandbox import validate_safe_path, sanitize_filename

        safe_path = validate_safe_path(
            filepath,
            allowed_roots=self.config.allowed_ingest_roots,
            allow_temp_dirs=True
        )
        filepath_str = str(safe_path)
        orig_filename = sanitize_filename(os.path.basename(filepath_str))
        file_hash = compute_file_hash(filepath_str)

        parser_result = parse_document(
            file_path=filepath_str,
            filename=orig_filename,
            ocr_threshold=self.config.ocr_threshold_chars,
            ocr_dpi=self.config.ocr_dpi,
            ocr_lang=self.config.ocr_lang,
            timeout=self.config.subprocess_timeout
        )

        chunks = chunk_document_pages(
            pages=parser_result.pages,
            filename=orig_filename,
            file_hash=file_hash,
            max_chars=max_chars or self.config.max_chars_per_chunk,
            overlap_chars=overlap_chars or self.config.overlap_chars,
            doc_type=doc_type,
            base_metadata={"library_mode": True},
            chunker_version="1.0",
            embed_model=self.config.embed_model or "none"
        )

        return parser_result, [c.to_dict() for c in chunks]

    def reparse(
        self,
        document_id: int,
        operator_token: Optional[str] = None,
        confirmation_token: Optional[str] = None
    ) -> IngestReport:
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

            ws = db.query(Workspace).filter(Workspace.id == doc.workspace_id).first()
            if ws and getattr(ws, "is_legal_hold", False):
                raise LegalHoldActiveError(f"CANNOT_REPARSE_LEGAL_HOLD_ACTIVE: Workspace '{ws.name}' is under active legal hold.")

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
            doc_file_hash = doc.file_hash
            ws_id = doc.workspace_id

            # Remove old document and cascaded chunks to force fresh reparse
            db.delete(doc)

            # Record operator audit entry
            try:
                audit = OperatorAudit(
                    action="reparse",
                    document_id=document_id,
                    workspace_id=ws_id,
                    confirmation_token=confirmation_token or operator_token or "unspecified",
                    details=json.dumps({"filename": doc_filename, "file_hash": doc_file_hash})
                )
                db.add(audit)
            except Exception as ae:
                logger.warning(f"Could not queue OperatorAudit record: {ae}")

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

    def delete_document(
        self,
        document_id: int,
        operator_token: Optional[str] = None,
        confirmation_token: Optional[str] = None
    ) -> bool:
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

            ws = db.query(Workspace).filter(Workspace.id == doc.workspace_id).first()
            if ws and getattr(ws, "is_legal_hold", False):
                raise LegalHoldActiveError(f"CANNOT_DELETE_LEGAL_HOLD_ACTIVE: Workspace '{ws.name}' is under active legal hold.")

            ws_id = doc.workspace_id
            doc_filename = doc.filename
            doc_file_hash = doc.file_hash

            db.delete(doc)

            # Record operator audit entry
            try:
                audit = OperatorAudit(
                    action="delete",
                    document_id=document_id,
                    workspace_id=ws_id,
                    confirmation_token=confirmation_token or operator_token or "unspecified",
                    details=json.dumps({"filename": doc_filename, "file_hash": doc_file_hash})
                )
                db.add(audit)
            except Exception as ae:
                logger.warning(f"Could not queue OperatorAudit record: {ae}")

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

    def get_document_lineage(self, filename: str, workspace: str) -> Dict[str, Any]:
        """
        Retrieve complete version lineage (v1 -> v2) for a document in a workspace,
        including section header diffs (added_headers, removed_headers, retained_headers).
        """
        db = self._get_db()
        try:
            ws = db.query(Workspace).filter(Workspace.name == workspace.strip()).first()
            if not ws:
                raise WorkspaceNotFound(f"Workspace '{workspace}' not found.")

            docs = db.query(Document).filter(
                Document.workspace_id == ws.id,
                Document.filename == filename.strip()
            ).order_by(Document.version.asc(), Document.id.asc()).all()

            if not docs:
                return {"filename": filename, "workspace": workspace, "version_count": 0, "versions": []}

            versions_data = []
            prior_headers = None
            for d in docs:
                chunks = db.query(DocumentChunk).filter(DocumentChunk.document_id == d.id).order_by(DocumentChunk.chunk_index.asc()).all()
                headers_seen = set()
                headers = []
                for c in chunks:
                    h = c.header or "General"
                    if h not in headers_seen:
                        headers_seen.add(h)
                        headers.append(h)

                diff = None
                if prior_headers is not None:
                    curr_set = set(headers)
                    prior_set = set(prior_headers)
                    diff = {
                        "prior_version": versions_data[-1]["version"],
                        "added_headers": [h for h in headers if h not in prior_set],
                        "removed_headers": [h for h in prior_headers if h not in curr_set],
                        "retained_headers": [h for h in headers if h in prior_set]
                    }

                versions_data.append({
                    "version": d.version,
                    "document_id": d.id,
                    "file_hash": d.file_hash,
                    "status": d.status,
                    "ingested_at": d.ingested_at.isoformat() if d.ingested_at else None,
                    "total_pages": d.total_pages,
                    "total_chunks": len(chunks),
                    "header_count": len(headers),
                    "headers": headers,
                    "diff_from_prior": diff
                })
                prior_headers = headers

            return {
                "filename": filename,
                "workspace": workspace,
                "version_count": len(versions_data),
                "versions": versions_data
            }
        finally:
            db.close()

    def search(
        self,
        query: str,
        workspace: str,
        doc_type: Optional[str] = None,
        limit: int = 5,
        filters: Optional[Union[SearchFilter, Dict[str, Any]]] = None,
        mode: str = "hybrid"
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
                config=self.config,
                mode=mode
            )
        finally:
            db.close()

    def explain(
        self,
        query: str,
        workspace: str,
        doc_type: Optional[str] = None,
        limit: int = 5,
        filters: Optional[Union[SearchFilter, Dict[str, Any]]] = None,
        mode: str = "hybrid"
    ) -> Dict[str, Any]:
        """
        Run hybrid retrieval diagnostics and return a detailed scoring scorecard.
        """
        import time
        start_time = time.time()
        hits = self.search(
            query=query,
            workspace=workspace,
            doc_type=doc_type,
            limit=limit,
            filters=filters,
            mode=mode
        )
        elapsed_ms = round((time.time() - start_time) * 1000, 2)

        scorecard = {
            "query": query,
            "workspace": workspace,
            "mode": mode,
            "latency_ms": elapsed_ms,
            "hits_count": len(hits),
            "hits": [
                {
                    "rank": idx + 1,
                    "chunk_id": h.chunk_id,
                    "document_id": h.document_id,
                    "filename": h.filename,
                    "locator": h.locator,
                    "citation": h.citation,
                    "score": round(h.score, 6),
                    "dense_score": round(h.dense_score, 4) if h.dense_score is not None else None,
                    "sparse_score": round(h.sparse_score, 4) if h.sparse_score is not None else None,
                    "vector_rank": h.vector_rank,
                    "fts_rank": h.fts_rank,
                    "section_boost": h.section_boost,
                    "phrase_boost": h.phrase_boost,
                    "match_reasons": h.match_reasons,
                    "snippet": (h.text[:140] + "...") if len(h.text) > 140 else h.text
                }
                for idx, h in enumerate(hits)
            ]
        }
        return scorecard

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
                    is_legal_hold=getattr(w, "is_legal_hold", False),
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

    def export_workspace(self, workspace: str, output_path: Optional[str] = None) -> str:
        """
        Export a complete workspace as a standalone .tar.gz archive.
        Ensures corpus portability without database lock-in.
        """
        from .workspace import export_workspace
        return export_workspace(
            workspace_name=workspace,
            output_path=output_path,
            config=self.config,
            engine=self.engine
        )

    def set_legal_hold(
        self,
        workspace: str,
        legal_hold: bool = True,
        operator_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """Toggle legal hold status on a workspace. Operator action."""
        if self.config.operator_token:
            if not operator_token or not secrets.compare_digest(operator_token, self.config.operator_token):
                raise AuthenticationError("Operator authorization required to change legal hold status.")

        db = self._get_db()
        try:
            ws = db.query(Workspace).filter(Workspace.name == workspace.strip()).first()
            if not ws:
                raise WorkspaceNotFound(f"Workspace '{workspace}' does not exist.")

            ws.is_legal_hold = bool(legal_hold)
            try:
                audit = OperatorAudit(
                    action="set_legal_hold",
                    workspace_id=ws.id,
                    confirmation_token=operator_token or "unspecified",
                    details=json.dumps({"workspace": ws.name, "legal_hold": ws.is_legal_hold})
                )
                db.add(audit)
            except Exception as ae:
                logger.warning(f"Could not queue OperatorAudit record: {ae}")

            db.commit()
            return {
                "workspace": ws.name,
                "is_legal_hold": ws.is_legal_hold,
                "status": "LEGAL_HOLD_ACTIVE" if ws.is_legal_hold else "LEGAL_HOLD_RELEASED"
            }
        finally:
            db.close()

    def purge_workspace(
        self,
        workspace: str,
        confirmation_token: Optional[str] = None,
        operator_token: Optional[str] = None
    ) -> bool:
        """
        Purge an entire workspace and all its documents, chunks, and ingest runs.
        Requires confirmation_token='CONFIRM_PURGE_<workspace>'.
        Strictly forbidden if workspace is under active legal hold.
        """
        expected = f"CONFIRM_PURGE_{workspace.strip()}"
        if confirmation_token != expected:
            raise ConfigurationError(f"purge_workspace requires confirmation_token='{expected}' to proceed.")

        if self.config.operator_token:
            if not operator_token or not secrets.compare_digest(operator_token, self.config.operator_token):
                raise AuthenticationError("Operator authorization required to purge workspace.")

        db = self._get_db()
        try:
            ws = db.query(Workspace).filter(Workspace.name == workspace.strip()).first()
            if not ws:
                return False

            if getattr(ws, "is_legal_hold", False):
                raise LegalHoldActiveError(f"CANNOT_PURGE_LEGAL_HOLD_ACTIVE: Workspace '{ws.name}' is under active legal hold.")

            ws_id = ws.id
            ws_name = ws.name

            try:
                audit = OperatorAudit(
                    action="purge_workspace",
                    workspace_id=ws_id,
                    confirmation_token=confirmation_token or operator_token or "unspecified",
                    details=json.dumps({"workspace": ws_name})
                )
                db.add(audit)
            except Exception as ae:
                logger.warning(f"Could not queue OperatorAudit record: {ae}")

            db.delete(ws)
            db.commit()
            return True
        finally:
            db.close()

    def export_legal_hold_bundle(self, workspace: str, output_path: Optional[str] = None) -> Dict[str, Any]:
        """Generate a signed cryptographic Legal Hold bundle."""
        from .workspace import export_legal_hold_bundle
        return export_legal_hold_bundle(
            workspace_name=workspace,
            output_path=output_path,
            config=self.config,
            engine=self.engine
        )

    def import_workspace(self, tarball_path: str, target_workspace: Optional[str] = None) -> Dict[str, Any]:
        """
        Import a workspace archive (.tar.gz) into the local database and archival store.
        """
        from .workspace import import_workspace
        return import_workspace(
            tarball_path=tarball_path,
            target_workspace=target_workspace,
            config=self.config,
            engine=self.engine
        )

    def list_poison_files(self, workspace: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List all failed/poison files across workspaces in .failed/ with error sidecar metadata.
        """
        from .ingest import list_poison_files
        return list_poison_files(
            watch_dir=self.config.watch_dir,
            workspace=workspace,
            allowed_roots=self.config.allowed_ingest_roots
        )

    def replay_poison_file(
        self,
        filename: str,
        workspace: str,
        doc_type: DocType = DocType.GENERAL
    ) -> IngestReport:
        """
        Replay a poisoned file from .failed/<workspace>/<filename>.
        If ingestion succeeds, remove the file from .failed/ and return IngestReport.
        """
        from .ingest import replay_poison_file
        pipeline = self._get_pipeline()
        return replay_poison_file(
            filename=filename,
            workspace_name=workspace,
            pipeline=pipeline,
            watch_dir=self.config.watch_dir,
            allowed_roots=self.config.allowed_ingest_roots,
            doc_type=doc_type
        )


# Canonical thin alias for backwards-compatibility
Nexus = NexusClient

