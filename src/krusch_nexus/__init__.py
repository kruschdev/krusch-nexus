"""
KruschNexus
===========
Universal Offline Document Ingestion Engine & Page-True Citation Spine.

A dedicated homelab corpus factory providing:
`file → parse → chunk → embed → persist → search with citations`
"""

__version__ = "1.0.0"

from .client import Nexus, NexusIngestClient
from .config import NexusConfig
from .models import IngestReport, ChunkHit, Citation, WorkspaceInfo, DocumentInfo
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
    FileOversizedError
)

__all__ = [
    "Nexus",
    "NexusIngestClient",
    "NexusConfig",
    "IngestReport",
    "ChunkHit",
    "Citation",
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
    "__version__"
]
