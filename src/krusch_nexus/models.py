"""
KruschNexus Typed Data Models
=============================
Strict Pydantic contracts defining public models for ingestion, citations,
search hits, parser results, and workspace metadata.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, model_validator


class DocType(str, Enum):
    """Document classification enum validated at the API boundary."""
    AUTHORITY = "authority"
    WORK_PRODUCT = "work_product"
    FACT_NARRATIVE = "fact_narrative"
    GENERAL = "general"


class Citation(BaseModel):
    """Structured, reproducible citation addressing an exact page or heading locator."""
    schema_version: str = "1.0"
    filename: str
    page_number: Optional[int] = None
    locator: Optional[str] = None
    header: Optional[str] = None

    def formatted(self) -> str:
        """
        Produce a canonical citation string.
        - PDF / paged: '{filename} p.{n} § {header}'
        - DOCX / CSV / HTML (unpaged): '{filename} § {locator or header}'
        """
        sec_label = self.locator if (self.page_number is None and self.locator) else (self.header or self.locator)
        clean_sec = ""
        if sec_label and sec_label.strip():
            s = sec_label.strip().lstrip("#").strip()
            if not s.startswith("§") and not s.lower().startswith("section") and not s.lower().startswith("row"):
                clean_sec = f"§ {s}"
            else:
                clean_sec = s

        if self.page_number is not None:
            if clean_sec:
                return f"{self.filename} p.{self.page_number} {clean_sec}"
            return f"{self.filename} p.{self.page_number}"
        else:
            if clean_sec:
                return f"{self.filename} {clean_sec}"
            return self.filename

    def __str__(self) -> str:
        return self.formatted()


class ContentBlock(BaseModel):
    """Structural block element within a page (Page Object Model)."""
    text: str
    block_type: str = "paragraph"  # "paragraph", "heading", "table_row", "header_footer"
    bbox: Optional[List[float]] = None
    confidence: Optional[float] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None


class PageData(BaseModel):
    """Represents a single parsed page or structural section in Page Object Model."""
    schema_version: str = "1.0"
    index: Optional[int] = None      # 1-based page number (None for unpaged files)
    locator: Optional[str] = None    # Heading hierarchy or row-group (e.g. "Art. IV > Sec. 8.22")
    text: str
    blocks: List[ContentBlock] = Field(default_factory=list)
    tables: List[Dict[str, Any]] = Field(default_factory=list)
    has_images: bool = False
    ocr_applied: bool = False
    confidence: Optional[float] = None  # Mean OCR confidence (0.0 - 1.0)
    char_count: int = 0

    @model_validator(mode="before")
    @classmethod
    def handle_page_number_alias(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "page_number" in data and "index" not in data:
                data["index"] = data.pop("page_number")
        return data

    @property
    def page_number(self) -> Optional[int]:
        return self.index


class ParserResult(BaseModel):
    """Standard contract returned by document parsers."""
    schema_version: str = "1.0"
    filename: str
    mime: str
    file_hash: str
    parser_name: str
    parser_version: str
    pages: List[PageData]
    warnings: List[str] = Field(default_factory=list)

    @property
    def total_pages(self) -> int:
        return len(self.pages)

    @property
    def total_chars(self) -> int:
        return sum(len(p.text) for p in self.pages)

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages if p.text)


class IngestRequest(BaseModel):
    """Request model for document ingestion."""
    path: Optional[str] = None
    workspace: str
    doc_type: DocType = DocType.GENERAL
    archive: bool = False


class IngestReport(BaseModel):
    """Frozen IngestReport contract."""
    schema_version: str = "1.0"
    status: str = "completed"  # "completed", "skipped_duplicate", "failed"
    document_id: Optional[int] = None
    filename: str
    workspace: str
    file_hash: str
    doc_type: str = "general"
    pages: int = 0
    chunks: int = 0
    total_pages: int = 0
    total_chunks: int = 0
    ocr_pages: List[int] = Field(default_factory=list)
    ocr_mean_confidence: Optional[float] = None
    duration_ms: float = 0.0
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    citation_preview: Optional[str] = None

    def model_post_init(self, __context: Any) -> None:
        if self.pages and not self.total_pages:
            self.total_pages = self.pages
        elif self.total_pages and not self.pages:
            self.pages = self.total_pages
        if self.chunks and not self.total_chunks:
            self.total_chunks = self.chunks
        elif self.total_chunks and not self.chunks:
            self.chunks = self.total_chunks

    @property
    def hash(self) -> str:
        return self.file_hash


class SearchFilter(BaseModel):
    """Librarian-style structural and metadata predicates for exact search filtering."""
    page: Optional[int] = None
    header_regex: Optional[str] = None
    doc_id: Optional[int] = None
    doc_type: Optional[str] = None
    filename: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None


class SearchHit(BaseModel):
    """Frozen search hit contract returned by retrieval with explainability metadata."""
    schema_version: str = "1.0"
    citation: str
    page_number: Optional[int] = None
    header: Optional[str] = None
    locator: Optional[str] = None
    score: float = 0.0
    text: str
    document_id: int
    chunk_id: int
    filename: Optional[str] = None
    workspace: Optional[str] = None
    chunk_index: Optional[int] = None
    dense_score: Optional[float] = None
    sparse_score: Optional[float] = None
    vector_rank: Optional[int] = None
    fts_rank: Optional[int] = None
    section_boost: bool = False
    lexical_boost: bool = False
    match_reasons: List[str] = Field(default_factory=list)
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    confidence: Optional[float] = None
    source_hash: Optional[str] = None
    file_hash: Optional[str] = None

    # Backward compatibility alias
    @property
    def content(self) -> str:
        return self.text

    @property
    def rrf_score(self) -> float:
        return self.score

    @property
    def citation_model(self) -> Citation:
        return Citation(
            filename=self.filename or "",
            page_number=self.page_number,
            locator=self.locator,
            header=self.header
        )


# Backward-compatible alias
ChunkHit = SearchHit


class WorkspaceInfo(BaseModel):
    """Metadata summary of a document workspace."""
    schema_version: str = "1.0"
    id: int
    name: str
    description: Optional[str] = None
    document_count: int = 0
    created_at: Optional[str] = None


class DocumentInfo(BaseModel):
    """Metadata summary of an ingested document."""
    schema_version: str = "1.0"
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
    chunker_version: Optional[str] = "1.0"
    created_at: Optional[str] = None
