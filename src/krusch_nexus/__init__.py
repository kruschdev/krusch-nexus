"""
KruschNexus
===========
Air-Gapped Universal Document Ingestion Engine & Citation Spine.

A dedicated homelab corpus factory providing:
`file → parse → chunk with provenance → embed locally → persist → hybrid search with citations`
"""

__version__ = "0.2.4"

from .client import NexusClient, Nexus
from .workspace import export_workspace, import_workspace, export_legal_hold_bundle
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
    LegalHoldActiveError
)

__all__ = [
    "NexusClient",
    "Nexus",
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
    "__version__"
]
