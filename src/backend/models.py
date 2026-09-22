"""
Backward-compatibility shim for src.backend.models.
Deprecated: Use 'from krusch_nexus.models import ...' instead.
"""
from krusch_nexus.models import (
    IngestReport,
    SearchHit,
    ChunkHit,
    Citation,
    WorkspaceInfo,
    DocumentInfo
)

__all__ = [
    "IngestReport",
    "SearchHit",
    "ChunkHit",
    "Citation",
    "WorkspaceInfo",
    "DocumentInfo"
]
