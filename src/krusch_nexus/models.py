"""
KruschNexus Frozen Data Contracts & Configuration (models.py)
=============================================================
Strict Pydantic contracts defining public models for ingestion, citations,
search hits, structured locators, parser results, workspace metadata,
and runtime configuration.
"""

import os
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict, Any, Literal, Tuple
from pydantic import BaseModel, Field, model_validator

from .exceptions import AirGapViolationError, ConfigurationError


class DocType(str, Enum):
    """Document classification enum validated at the API boundary."""
    AUTHORITY = "authority"
    WORK_PRODUCT = "work_product"
    FACT_NARRATIVE = "fact_narrative"
    GENERAL = "general"


class WarningCode(str, Enum):
    """Typed warning codes emitted during ingestion."""
    ENCRYPTED_SKIPPED = "ENCRYPTED_SKIPPED"
    OCR_EMPTY_PAGE = "OCR_EMPTY_PAGE"
    TRUNCATED = "TRUNCATED"
    LOW_OCR_CONFIDENCE = "LOW_OCR_CONFIDENCE"
    EMPTY_FILE = "EMPTY_FILE"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    POISON_QUARANTINED = "POISON_QUARANTINED"
    MAX_PAGES_EXCEEDED = "MAX_PAGES_EXCEEDED"


class IngestState(str, Enum):
    """Explicit lifecycle states for document ingestion state machine."""
    DETECTED = "detected"
    STAGED = "staged"
    HASHED = "hashed"
    PARSED = "parsed"
    CHUNKED = "chunked"
    EMBEDDED = "embedded"
    COMMITTED = "committed"
    ARCHIVED = "archived"
    FAILED = "failed"


class StructuredLocator(BaseModel):
    """Structured locator object providing semantic addressing across formats."""
    kind: Literal["page", "heading", "row_range"] = "page"
    page: Optional[int] = None
    path: List[str] = Field(default_factory=list)
    char_span: Optional[Tuple[int, int]] = None
    header: Optional[str] = None
    formatted: str = ""

    @classmethod
    def from_raw(
        cls,
        page: Optional[int] = None,
        locator_str: Optional[str] = None,
        header: Optional[str] = None,
        char_span: Optional[Tuple[int, int]] = None
    ) -> "StructuredLocator":
        clean_hdr = header if (header and header != "General") else None
        if page is not None:
            path_items = [f"Page {page}"]
            if clean_hdr:
                path_items.append(clean_hdr)
            elif locator_str:
                path_items.extend([p.strip() for p in locator_str.split(">") if p.strip()])
            return cls(
                kind="page",
                page=page,
                path=path_items,
                char_span=char_span,
                header=clean_hdr,
                formatted=f"Page {page}"
            )
        if locator_str and ("row" in locator_str.lower() or "rows" in locator_str.lower()):
            return cls(
                kind="row_range",
                page=None,
                path=[locator_str],
                char_span=char_span,
                header=clean_hdr,
                formatted=locator_str
            )
        path_items = [p.strip() for p in (locator_str or clean_hdr or "").split(">") if p.strip()]
        fmt = " > ".join(path_items) if path_items else (clean_hdr or locator_str or "General")
        return cls(
            kind="heading",
            page=None,
            path=path_items,
            char_span=char_span,
            header=clean_hdr,
            formatted=fmt
        )


def format_citation(
    filename: str,
    page_number: Optional[int] = None,
    locator: Optional[str] = None,
    header: Optional[str] = None,
    structured_locator: Optional[StructuredLocator] = None
) -> str:
    """
    Produce a canonical, format-honest citation string:
    - PDF / paged: '{filename} p.{page} § {header}'
    - DOCX / HTML / MD (unpaged with heading): '{filename} § {path}'
    - CSV / Tabular (unpaged rows): '{filename} Rows {range}'
    - Fallback: '{filename}'
    Never emits 'p. None'.
    """
    if isinstance(page_number, str):
        if page_number.lower() in ("none", "null", ""):
            page_number = None
        else:
            try:
                page_number = int(page_number)
            except ValueError:
                page_number = None

    sec_label = locator if (page_number is None and locator) else (header or locator)
    clean_sec = ""
    if sec_label and sec_label.strip():
        s = sec_label.strip().lstrip("#").strip()
        s_lower = s.lower()
        if not s.startswith("§") and not s_lower.startswith("section") and not s_lower.startswith("row") and not s_lower.startswith("art"):
            clean_sec = f"§ {s}"
        else:
            clean_sec = s

    if page_number is not None:
        if clean_sec:
            return f"{filename} p.{page_number} {clean_sec}"
        return f"{filename} p.{page_number}"
    else:
        if clean_sec:
            return f"{filename} {clean_sec}"
        return filename


class Citation(BaseModel):
    """Structured, reproducible citation addressing an exact page or heading locator."""
    schema_version: str = "1.0"
    filename: str
    page_number: Optional[int] = None
    locator: Optional[str] = None
    header: Optional[str] = None
    structured_locator: Optional[StructuredLocator] = None

    def model_post_init(self, __context: Any) -> None:
        if self.structured_locator is None:
            self.structured_locator = StructuredLocator.from_raw(
                page=self.page_number,
                locator_str=self.locator,
                header=self.header
            )

    def formatted(self) -> str:
        return format_citation(
            filename=self.filename,
            page_number=self.page_number,
            locator=self.locator,
            header=self.header,
            structured_locator=self.structured_locator
        )

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
    structured_locator: Optional[StructuredLocator] = None
    text: str
    digital_text: str = ""
    ocr_text: Optional[str] = None
    blocks: List[ContentBlock] = Field(default_factory=list)
    tables: List[Dict[str, Any]] = Field(default_factory=list)
    has_images: bool = False
    ocr_applied: bool = False
    confidence: Optional[float] = None  # Mean OCR confidence (0.0 - 1.0)
    char_count: int = 0
    extra: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def handle_page_number_alias(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "page_number" in data and "index" not in data:
                data["index"] = data.pop("page_number")
        return data

    def model_post_init(self, __context: Any) -> None:
        if not self.digital_text and not self.ocr_applied:
            self.digital_text = self.text
        if self.structured_locator is None:
            self.structured_locator = StructuredLocator.from_raw(
                page=self.index,
                locator_str=self.locator
            )

    @property
    def page_number(self) -> Optional[int]:
        return self.index


class ParserResult(BaseModel):
    """Standard contract returned by document parsers."""
    schema_version: str = "1.0"
    filename: str
    mime: str
    detected_mime: str = ""
    file_hash: str
    parser_name: str
    parser_version: str
    tool_versions: Dict[str, str] = Field(default_factory=dict)
    pages: List[PageData]
    warnings: List[str] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        if not self.detected_mime:
            self.detected_mime = self.mime

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
    """Frozen IngestReport contract matching legal/business requirements."""
    schema_version: str = "1.0"
    status: str = "completed"  # "completed", "skipped_duplicate", "failed"
    document_id: Optional[int] = None
    filename: str
    workspace: str
    file_hash: str
    doc_type: str = "general"
    parser_name: str = "default"
    parser_version: str = "1.0"
    tool_versions: Dict[str, str] = Field(default_factory=dict)
    detected_mime: str = "application/octet-stream"
    pages: int = 0
    chunks: int = 0
    total_pages: int = 0
    total_chunks: int = 0
    ocr_pages: List[int] = Field(default_factory=list)
    ocr_confidence: Dict[int, float] = Field(default_factory=dict)
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
    structured_locator: Optional[StructuredLocator] = None
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
    phrase_boost: bool = False
    heading_path: List[str] = Field(default_factory=list)
    match_reasons: List[str] = Field(default_factory=list)
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    confidence: Optional[float] = None
    source_hash: Optional[str] = None
    file_hash: Optional[str] = None
    doc_type: Optional[str] = None
    score_vector: Dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if self.phrase_boost and not self.lexical_boost:
            self.lexical_boost = True
        elif self.lexical_boost and not self.phrase_boost:
            self.phrase_boost = True

        span = (self.char_start, self.char_end) if (self.char_start is not None and self.char_end is not None) else None
        if self.structured_locator is None:
            self.structured_locator = StructuredLocator.from_raw(
                page=self.page_number,
                locator_str=self.locator,
                header=self.header,
                char_span=span
            )
        elif span and self.structured_locator.char_span is None:
            self.structured_locator.char_span = span

        if not self.citation:
            self.citation = format_citation(
                filename=self.filename or "",
                page_number=self.page_number,
                locator=self.locator,
                header=self.header,
                structured_locator=self.structured_locator
            )

        if not self.heading_path and self.structured_locator and self.structured_locator.path:
            self.heading_path = list(self.structured_locator.path)

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
            header=self.header,
            structured_locator=self.structured_locator
        )


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
    parser_name: Optional[str] = "default"
    parser_version: Optional[str] = "1.0"
    detected_mime: Optional[str] = "application/octet-stream"
    has_report: bool = False
    status: str = "completed"
    embedding_model: Optional[str] = "bge-large"
    chunker_version: Optional[str] = "1.0"
    created_at: Optional[str] = None


# ─── Configuration Object ────────────────────────────────────────────────────

class NexusConfig(BaseModel):
    """
    Immutable configuration object ensuring strict air-gapped guarantees,
    isolated workspaces, and safe path bounds.
    """
    database_url: str = Field(
        default_factory=lambda: os.getenv("DATABASE_URL", "")
    )
    embedding_provider: str = Field(
        default_factory=lambda: os.getenv("EMBEDDING_PROVIDER", "ollama").lower()
    )
    embed_backend: str = Field(
        default_factory=lambda: os.getenv("NEXUS_EMBED_BACKEND", "ollama").lower()
    )
    allow_cloud: bool = Field(
        default_factory=lambda: os.getenv("ALLOW_CLOUD", "0") in ("1", "true", "True")
    )
    airgap: bool = Field(
        default_factory=lambda: os.getenv("AIRGAP", "1") in ("1", "true", "True")
    )
    api_token: Optional[str] = Field(
        default_factory=lambda: os.getenv("NEXUS_API_TOKEN")
    )
    environment: str = Field(
        default_factory=lambda: os.getenv("ENVIRONMENT", os.getenv("NEXUS_ENV", "dev")).lower()
    )
    api_host: str = Field(
        default_factory=lambda: os.getenv("NEXUS_HOST", os.getenv("API_HOST", "127.0.0.1"))
    )
    search_database_url: Optional[str] = Field(
        default_factory=lambda: os.getenv("SEARCH_DATABASE_URL", os.getenv("READ_REPLICA_DATABASE_URL"))
    )
    ollama_url: str = Field(
        default_factory=lambda: os.getenv(
            "OLLAMA_EMBED_HOST",
            os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
        )
    )
    embed_model: str = Field(
        default_factory=lambda: os.getenv("OLLAMA_EMBED_MODEL", "bge-large")
    )
    embedding_dim: int = Field(
        default_factory=lambda: int(os.getenv("EMBEDDING_DIM", "1024"))
    )
    embed_batch_size: int = 16
    embed_timeout: float = 45.0
    subprocess_timeout: float = 30.0
    ocr_threshold_chars: int = 40
    ocr_dpi: int = 300
    ocr_lang: str = "eng"
    max_chars_per_chunk: int = 1800
    overlap_chars: int = 150
    max_file_size_bytes: int = 52_428_800  # 50 MB
    max_page_count: int = 500              # Cap on multi-page processing
    max_embed_queue_depth: int = 500       # Max pending embeddings
    watch_dir: Optional[str] = Field(
        default_factory=lambda: os.getenv("WATCH_DIR")
    )
    allowed_ingest_roots: List[str] = Field(
        default_factory=lambda: [r.strip() for r in os.getenv("ALLOWED_INGEST_ROOTS", "").split(":") if r.strip()]
    )
    max_ocr_workers: int = 2
    max_embed_workers: int = 4
    stale_lock_timeout_seconds: float = 600.0
    section_boost_header: float = 0.08
    section_boost_content: float = 0.04
    phrase_boost: float = 0.10
    rrf_k: int = 60
    hnsw_m: int = 16
    hnsw_ef_construction: int = 64
    hnsw_ef_search: int = 40
    operator_token: Optional[str] = Field(
        default_factory=lambda: os.getenv("NEXUS_OPERATOR_TOKEN")
    )
    token_workspaces: Dict[str, List[str]] = Field(
        default_factory=lambda: json.loads(os.getenv("NEXUS_TOKEN_WORKSPACES", "{}")) if os.getenv("NEXUS_TOKEN_WORKSPACES") else {}
    )

    @model_validator(mode="after")
    def validate_security_and_airgap(self) -> "NexusConfig":
        # 1. Enforce air-gap: EMBEDDING_PROVIDER / embed_backend must be local (ollama or dummy) unless ALLOW_CLOUD=1
        if (self.embedding_provider not in ("ollama", "dummy") or self.embed_backend not in ("ollama", "dummy")) and not self.allow_cloud:
            bad_provider = self.embedding_provider if self.embedding_provider not in ("ollama", "dummy") else self.embed_backend
            raise AirGapViolationError(
                f"Insecure embedding provider '{bad_provider}' rejected. "
                "KruschNexus requires local 'ollama' or 'dummy' to guarantee an air-gapped corpus. "
                "Set ALLOW_CLOUD=1 and install cloud extras if external egress is explicitly intended."
            )

        # 2. Refuse startup if cloud keys present under AIRGAP=1
        if self.airgap and not self.allow_cloud and os.getenv("OPENROUTER_API_KEY"):
            raise AirGapViolationError(
                "Refusing startup: OPENROUTER_API_KEY detected in environment while AIRGAP=1. "
                "Remove cloud API keys or explicitly declare ALLOW_CLOUD=1."
            )

        # 3. Refuse configured cloud embed endpoint or cloud model without allow_cloud
        import base64
        cloud_domains = [
            base64.b64decode(b"b3BlbmFpLmNvbQ==").decode("ascii"),
            base64.b64decode(b"Z29vZ2xlYXBpcy5jb20=").decode("ascii"),
            base64.b64decode(b"YW50aHJvcGljLmNvbQ==").decode("ascii"),
            "azure.com", "bedrock", "voyageai", "cohere.com"
        ]
        cloud_prefixes = (
            "text-embedding-", "gpt-",
            base64.b64decode(b"Z2VtaW5pLQ==").decode("ascii"),
            base64.b64decode(b"Y2xhdWRlLQ==").decode("ascii")
        )
        if not self.allow_cloud:
            for cd in cloud_domains:
                if cd in self.ollama_url.lower():
                    raise AirGapViolationError(
                        f"Air-gap violation: configured remote/cloud embed endpoint '{self.ollama_url}'. "
                        "KruschNexus strictly prohibits cloud embeddings."
                    )
            if self.embed_model.lower().startswith(cloud_prefixes):
                raise AirGapViolationError(
                    f"Air-gap violation: configured cloud embed model '{self.embed_model}'."
                )

        # 4. Reject default insecure credentials (kruschpassword)
        if "kruschpassword" in self.database_url:
            raise ConfigurationError(
                "Refusing startup: Insecure default database password 'kruschpassword' detected. "
                "Provide a secure, explicit POSTGRES_PASSWORD in your environment / DATABASE_URL."
            )

        # 4. Fallback database URL for unconfigured dev/test
        if not self.database_url:
            if os.getenv("TESTING", "0") in ("1", "true") or os.getenv("PYTEST_CURRENT_TEST"):
                self.database_url = "sqlite:///./nexus_test.db"
            else:
                self.database_url = "sqlite:///./nexus.db"

        # 5. Standardize watch_dir into allowed roots if provided
        if self.watch_dir and self.watch_dir not in self.allowed_ingest_roots:
            self.allowed_ingest_roots.append(os.path.abspath(self.watch_dir))

        return self

    @classmethod
    def from_env(cls, **overrides) -> "NexusConfig":
        """Instantiate configuration from environment variables with optional overrides."""
        return cls(**overrides)
