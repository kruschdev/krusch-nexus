"""
KruschNexus Typed Data Models
=============================
Strict Pydantic contracts defining the public API for ingestion reports,
canonical citations, search results (ChunkHit), and workspace metadata.
"""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class Citation(BaseModel):
    """Structured, reproducible citation addressing an exact page and heading."""
    filename: str
    page_number: int = Field(ge=1)
    header: Optional[str] = None

    def formatted(self) -> str:
        """Stable citation format: '{filename} p.{n} § {header}'."""
        if self.header and self.header.strip():
            h = self.header.strip()
            # Clean leading hashes or duplicated section symbols
            h_clean = h.lstrip("#").strip()
            if not h_clean.startswith("§") and not h_clean.lower().startswith("section"):
                sec_part = f"§ {h_clean}"
            else:
                sec_part = h_clean
            return f"{self.filename} p.{self.page_number} {sec_part}"
        return f"{self.filename} p.{self.page_number}"

    def __str__(self) -> str:
        return self.formatted()


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


class ChunkHit(BaseModel):
    """A grounded search result carrying canonical page/section citations."""
    chunk_id: int
    document_id: int
    workspace: str
    filename: str
    page_number: int
    chunk_index: int
    header: Optional[str] = None
    content: str
    citation: str  # formatted e.g. "{filename} p.{n} § {header}"
    dense_score: Optional[float] = None
    sparse_score: Optional[float] = None
    rrf_score: float = 0.0
    source_hash: Optional[str] = None
    file_hash: Optional[str] = None

    @property
    def citation_model(self) -> Citation:
        return Citation(
            filename=self.filename,
            page_number=self.page_number,
            header=self.header
        )


# Backward-compatible alias
SearchHit = ChunkHit


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
    status: str = "completed"
    embedding_model: Optional[str] = "bge-large"
    created_at: Optional[str] = None
