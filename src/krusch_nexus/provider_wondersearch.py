"""
Wondersearch Cloud Provider for KruschNexus (provider_wondersearch.py)
======================================================================
Implements zero-vendor-lockin RAG retrieval against Evokoa / Polygres Cloud.
Maps remote Wondersearch responses into KruschNexus' frozen Pydantic contracts
(SearchHit v1, IngestReport v1).
"""

from __future__ import annotations

import os
import uuid
import hashlib
import logging
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, timezone

import httpx

from .models import (
    NexusConfig,
    SearchHit,
    SearchFilter,
    IngestReport,
    DocType,
    format_citation
)
from .exceptions import (
    ConfigurationError,
    WorkspaceRequiredError,
    ParseError
)

logger = logging.getLogger("krusch_nexus.wondersearch")


class WondersearchProvider:
    """
    Decoupled cloud provider connecting KruschNexus to Dale's Wondersearch engine
    powered by Polygres Cloud.
    """

    def __init__(self, config: NexusConfig):
        self.config = config
        self.api_key = config.wondersearch_api_key or os.getenv("WONDERSEARCH_API_KEY") or os.getenv("POLYGRES_API_KEY")
        if not self.api_key:
            raise ConfigurationError(
                "Wondersearch provider requires an API key. "
                "Set WONDERSEARCH_API_KEY or POLYGRES_API_KEY in environment or NexusConfig."
            )

        self.base_url = (
            config.wondersearch_base_url
            or os.getenv("WONDERSEARCH_BASE_URL")
            or os.getenv("POLYGRES_BASE_URL")
            or "https://api.wondersearch.ai"
        ).rstrip("/")

        self.workspace_id = (
            config.wondersearch_workspace_id
            or os.getenv("WONDERSEARCH_WORKSPACE_ID")
        )
        self.drive_mapping = dict(config.wondersearch_drive_mapping or {})

    def _headers(self, idempotency_key: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "KruschNexus-Provider/0.2.5"
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        else:
            headers["Idempotency-Key"] = f"nexus-{uuid.uuid4().hex[:16]}"
        return headers

    def resolve_drive_id(self, workspace_name: str) -> Optional[str]:
        """
        Resolve a Krusch workspace name (e.g. 'Matter_Oakland') to a Wondersearch drive UUID.
        """
        # 1. Explicit drive mapping from config
        if workspace_name in self.drive_mapping:
            return self.drive_mapping[workspace_name]

        # 2. If workspace string is already a UUID format, use directly
        try:
            uuid.UUID(workspace_name)
            return workspace_name
        except ValueError:
            pass

        return None

    def _uuid_to_int_id(self, val: str) -> int:
        """Convert a string or UUID into a stable positive 31-bit integer for SearchHit schema."""
        return int(hashlib.md5(val.encode("utf-8")).hexdigest()[:8], 16) & 0x7FFFFFFF

    def search(
        self,
        query: str,
        workspace: str,
        doc_type: Optional[str] = None,
        limit: int = 5,
        filters: Optional[Union[SearchFilter, Dict[str, Any]]] = None,
        effort: str = "medium",
        group_by_document: bool = True,
        timeout_ms: Optional[int] = None
    ) -> List[SearchHit]:
        """
        Execute search against Wondersearch Cloud API and return canonical Krusch SearchHit models.
        """
        if not workspace or not workspace.strip():
            raise WorkspaceRequiredError("A target workspace is required for search.")

        target_workspace = workspace.strip()
        drive_id = self.resolve_drive_id(target_workspace)

        # Decide whether to hit drive search or workspace default drive search
        if drive_id:
            search_url = f"{self.base_url}/v1/drives/{drive_id}/search"
        elif self.workspace_id:
            search_url = f"{self.base_url}/v1/workspaces/{self.workspace_id}/search"
        else:
            raise ConfigurationError(
                f"Cannot route search: No Wondersearch drive mapped for workspace '{target_workspace}' "
                "and no WONDERSEARCH_WORKSPACE_ID configured."
            )

        payload: Dict[str, Any] = {
            "query": query,
            "effort": effort,
            "limit": limit,
            "group_by_document": group_by_document
        }

        if timeout_ms:
            payload["timeout_ms"] = timeout_ms

        if isinstance(filters, SearchFilter):
            resolved_filters = filters.model_dump(exclude_none=True)
        elif isinstance(filters, dict):
            resolved_filters = dict(filters)
        else:
            resolved_filters = {}

        if "folder_id" in resolved_filters:
            payload["folder_id"] = resolved_filters["folder_id"]

        headers = self._headers()
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(search_url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Wondersearch API HTTP error: {e.response.status_code} - {e.response.text}")
            raise ParseError(f"Wondersearch retrieval error: {e.response.status_code}") from e
        except Exception as e:
            logger.error(f"Wondersearch connection failure: {e}")
            raise ParseError(f"Wondersearch connection failure: {e}") from e

        results = data.get("results", [])
        usage = data.get("usage", {})
        cost_meta = usage.get("cost", {})

        hits: List[SearchHit] = []
        for rank, res in enumerate(results, start=1):
            doc_id_raw = str(res.get("document_id", f"doc_{rank}"))
            passage_id_raw = str(res.get("passage_id", f"pass_{rank}"))
            doc_id_int = self._uuid_to_int_id(doc_id_raw)
            chunk_id_int = self._uuid_to_int_id(passage_id_raw)

            external_id = res.get("external_id")
            metadata = res.get("metadata") or {}
            filename = metadata.get("filename") or external_id or f"document_{doc_id_raw[:8]}"

            start_byte = res.get("start_byte")
            end_byte = res.get("end_byte")
            locator_str = f"bytes {start_byte}..{end_byte}" if (start_byte is not None and end_byte is not None) else None

            revision = str(res.get("document_revision", "1"))
            score = float(res.get("score", 0.0))
            text_snippet = res.get("text", "")

            header_val = metadata.get("header")
            loc_val = header_val if header_val else locator_str

            citation_str = format_citation(
                filename=filename,
                locator=loc_val,
                header=header_val
            )

            hit = SearchHit(
                citation=citation_str,
                score=score,
                text=text_snippet,
                document_id=doc_id_int,
                chunk_id=chunk_id_int,
                filename=filename,
                workspace=target_workspace,
                chunk_index=rank,
                dense_score=score,
                char_start=start_byte,
                char_end=end_byte,
                header=header_val,
                locator=loc_val,
                source_hash=revision,
                doc_type=doc_type or metadata.get("doc_type", "general"),
                match_reasons=["wondersearch_cloud_vector"],
                score_vector={
                    "wondersearch_score": score,
                    "cost": cost_meta,
                    "request_id": data.get("request_id")
                }
            )
            hits.append(hit)

        return hits

    def ingest(
        self,
        filepath: str,
        workspace: str,
        doc_type: DocType = DocType.GENERAL,
        archive: bool = False
    ) -> IngestReport:
        """
        Ingest a document into Wondersearch drive via Content API.
        """
        if not workspace or not workspace.strip():
            raise WorkspaceRequiredError("A workspace name is required to ingest documents.")

        target_workspace = workspace.strip()
        drive_id = self.resolve_drive_id(target_workspace)

        if not drive_id:
            if not self.workspace_id:
                raise ConfigurationError(
                    f"Cannot ingest into Wondersearch: No drive mapped for '{target_workspace}' "
                    "and no WONDERSEARCH_WORKSPACE_ID configured to provision drives."
                )
            drive_id = self.create_or_get_drive(target_workspace)

        if not os.path.exists(filepath):
            raise ParseError(f"Target file '{filepath}' does not exist on disk.")

        filename = os.path.basename(filepath)
        with open(filepath, "rb") as f:
            raw_bytes = f.read()

        file_hash = hashlib.sha256(raw_bytes).hexdigest()

        # Text extraction for Content API document payload
        try:
            content_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            # Fallback for binary formats (e.g. PDF/DOCX) using local text extractor
            from .parsers import parse_document
            parsed = parse_document(filepath)
            content_text = "\n\n".join(p.text for p in parsed.pages if p.text)

        payload = {
            "external_id": filename,
            "text": content_text,
            "metadata": {
                "filename": filename,
                "doc_type": doc_type.value if hasattr(doc_type, "value") else str(doc_type),
                "sha256": file_hash,
                "ingested_by": "KruschNexus"
            }
        }

        url = f"{self.base_url}/v1/drives/{drive_id}/documents"
        headers = self._headers()
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                res_data = resp.json()
        except Exception as e:
            logger.error(f"Wondersearch ingestion failed: {e}")
            raise ParseError(f"Wondersearch upload failed: {e}") from e

        doc_uuid = res_data.get("id") or res_data.get("document_id") or uuid.uuid4().hex
        doc_id_int = self._uuid_to_int_id(str(doc_uuid))

        return IngestReport(
            status="completed",
            document_id=doc_id_int,
            filename=filename,
            workspace=target_workspace,
            file_hash=file_hash,
            doc_type=doc_type.value if hasattr(doc_type, "value") else str(doc_type),
            parser_name="wondersearch_cloud",
            parser_version="1.0",
            detected_mime="text/plain",
            pages=1,
            chunks=1,
            total_pages=1,
            total_chunks=1,
            citation_preview=f"{filename} § wondersearch:{doc_uuid[:8]}"
        )

    def create_or_get_drive(self, drive_name: str) -> str:
        """Create a new drive in the current Wondersearch workspace or find an existing one."""
        if not self.workspace_id:
            raise ConfigurationError("WONDERSEARCH_WORKSPACE_ID is required to manage drives.")

        list_url = f"{self.base_url}/v1/workspaces/{self.workspace_id}/drives"
        headers = self._headers()
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.get(list_url, headers=headers)
                if resp.status_code == 200:
                    drives = resp.json().get("drives", [])
                    for d in drives:
                        if d.get("name") == drive_name:
                            self.drive_mapping[drive_name] = d["id"]
                            return d["id"]

                # Not found, create it
                create_payload = {"name": drive_name}
                create_resp = client.post(list_url, json=create_payload, headers=headers)
                create_resp.raise_for_status()
                new_drive = create_resp.json()
                new_id = new_drive["id"]
                self.drive_mapping[drive_name] = new_id
                return new_id
        except Exception as e:
            logger.error(f"Could not provision Wondersearch drive: {e}")
            raise ConfigurationError(f"Failed to create/get drive '{drive_name}': {e}") from e
