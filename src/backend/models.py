"""
KruschNexus Data Models
=======================
Strict Pydantic models defining the public API contract for ingestion,
search results with canonical citations, and workspace/document metadata.
"""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class IngestReport(BaseModel):
    """Standardized report produced on every file ingestion attempt."""
    status: str  # "completed", "skipped_duplicate", "failed"
    document_id: Optional[int] = None
    filename: str
    workspace: str
    file_hash: str
    doc_type: str = "general"
    total_pages: int = 0
    total_chunks: int = 0
    ocr_pages: List[int] = Field(default_factory=list)
    duration_ms: float = 0.0
    error: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    citation_preview: Optional[str] = None


class SearchHit(BaseModel):
    """A grounded search result carrying canonical page/section citations."""
    chunk_id: int
    document_id: int
    workspace: str
    filename: str
    page_number: int
    chunk_index: int
    header: Optional[str] = None
    content: str
    citation: str  # e.g. "[lease.pdf, p. 14, § 8.22]"
    dense_score: Optional[float] = None
    sparse_score: Optional[float] = None
    rrf_score: float = 0.0
    source_hash: Optional[str] = None
    file_hash: Optional[str] = None


class WorkspaceInfo(BaseModel):
    """Metadata summary of a document workspace."""
    id: int
    name: str
    description: Optional[str] = None
    document_count: int = 0
    created_at: Optional[str] = None


class DocumentInfo(BaseModel):
    """Metadata summary of an ingested document."""
    id: int
    workspace_id: int
    workspace_name: str
    filename: str
    file_hash: str
    doc_type: str
    total_pages: int
    total_chunks: int
    has_report: bool = False
    created_at: Optional[str] = None
