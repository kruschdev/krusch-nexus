"""
KruschNexus
===========
Air-Gapped Universal Document Ingestion Engine & Citation Spine.

A dedicated homelab corpus factory providing:
`file → parse → chunk with provenance → embed locally → persist → hybrid search with citations`
"""

__version__ = "0.2.0"

from .client import NexusClient, Nexus, NexusIngestClient
from .config import NexusConfig
from .models import (
    IngestRequest,
    IngestReport,
    SearchHit,
    ChunkHit,
    Citation,
    DocType,
    PageData,
    ParserResult,
    WorkspaceInfo,
    DocumentInfo
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
    AuthenticationError
)

__all__ = [
    "NexusClient",
    "Nexus",
    "NexusIngestClient",
    "NexusConfig",
    "IngestRequest",
    "IngestReport",
    "SearchHit",
    "ChunkHit",
    "Citation",
    "DocType",
    "PageData",
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
    "__version__"
]
