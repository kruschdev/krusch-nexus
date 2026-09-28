"""
KruschNexus
===========
Air-Gapped Universal Document Ingestion Engine & Citation Spine.

A dedicated homelab corpus factory providing:
`file → parse → chunk with provenance → embed locally → persist → hybrid search with citations`
"""

__version__ = "0.2.5"

from typing import Optional, List, Dict, Any, Tuple

from .client import NexusClient, Nexus
from .workspace import export_workspace, import_workspace, export_legal_hold_bundle
from .chunking import chunk_document_pages, iter_chunk_document_pages
from .models import (
    NexusConfig,
    IngestRequest,
    IngestReport,
    SearchHit,
    SearchFilter,
    Citation,
    StructuredLocator,
    DocType,
    WarningCode,
    IngestState,
    PageData,
    ContentBlock,
    ParserResult,
    WorkspaceInfo,
    DocumentInfo,
    format_citation
)
from .exceptions import (
    NexusError,
    AirGapViolationError,
    PathSandboxError,
    WorkspaceRequiredError,
    ConfigurationError,
    ParseError,
    DuplicateDocument,
    WorkspaceNotFound,
    EmbeddingUnavailable,
    FileOversizedError,
    TooLargeError,
    EncryptedPdfError,
    EmptyOcrError,
    UnsupportedMimeError,
    CorruptedFileError,
    AuthenticationError,
    ModelDimensionDriftError,
    LegalHoldActiveError,
    PackValidationError
)
from .pack_exporter import (
    export_authority_pack,
    extract_grounded_slots,
    AuthorityPackExporter,
    PackValidator,
    PackSku,
    SourceSpan,
    SpanGroundedSlot
)


def parse_file(
    filepath: str,
    ocr_threshold: Optional[int] = None,
    ocr_dpi: Optional[int] = None,
    ocr_lang: Optional[str] = None,
    timeout: float = 30.0,
    pdf_backend: str = "poppler"
) -> ParserResult:
    """Zero-config library entrypoint: Parse a local document into format-honest PageData objects."""
    import os
    from .parsers import parse_document
    from .ingest.sandbox import sanitize_filename
    orig_filename = sanitize_filename(os.path.basename(filepath))
    return parse_document(
        file_path=str(filepath),
        filename=orig_filename,
        ocr_threshold=ocr_threshold,
        ocr_dpi=ocr_dpi,
        ocr_lang=ocr_lang,
        timeout=timeout,
        pdf_backend=pdf_backend
    )


def parse_and_chunk_file(
    filepath: str,
    doc_type: DocType = DocType.GENERAL,
    max_chars: int = 1800,
    overlap_chars: int = 150
) -> Tuple[ParserResult, List[Dict[str, Any]]]:
    """Zero-config library entrypoint: Parse and chunk a document with format-honest locators and bounding boxes."""
    import os
    from .parsers import parse_document
    from .parsers.registry import compute_file_hash
    from .ingest.sandbox import sanitize_filename
    from .chunking import chunk_document_pages

    orig_filename = sanitize_filename(os.path.basename(filepath))
    file_hash = compute_file_hash(filepath)
    res = parse_document(filepath, orig_filename)
    chunks = chunk_document_pages(
        pages=res.pages,
        filename=orig_filename,
        file_hash=file_hash,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
        doc_type=doc_type,
        base_metadata={"library_mode": True}
    )
    return res, [c.to_dict() for c in chunks]


__all__ = [
    "NexusClient",
    "Nexus",
    "parse_file",
    "parse_and_chunk_file",
    "chunk_document_pages",
    "iter_chunk_document_pages",
    "NexusConfig",
    "IngestRequest",
    "IngestReport",
    "SearchHit",
    "SearchFilter",
    "Citation",
    "StructuredLocator",
    "format_citation",
    "export_workspace",
    "import_workspace",
    "export_legal_hold_bundle",
    "DocType",
    "WarningCode",
    "IngestState",
    "PageData",
    "ContentBlock",
    "ParserResult",
    "WorkspaceInfo",
    "DocumentInfo",
    "NexusError",
    "AirGapViolationError",
    "PathSandboxError",
    "WorkspaceRequiredError",
    "ConfigurationError",
    "ParseError",
    "DuplicateDocument",
    "WorkspaceNotFound",
    "EmbeddingUnavailable",
    "FileOversizedError",
    "TooLargeError",
    "EncryptedPdfError",
    "EmptyOcrError",
    "UnsupportedMimeError",
    "CorruptedFileError",
    "AuthenticationError",
    "ModelDimensionDriftError",
    "LegalHoldActiveError",
    "PackValidationError",
    "export_authority_pack",
    "extract_grounded_slots",
    "AuthorityPackExporter",
    "PackValidator",
    "PackSku",
    "SourceSpan",
    "SpanGroundedSlot",
    "__version__"
]
