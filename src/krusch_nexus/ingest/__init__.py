"""
KruschNexus Modular Ingestion Package
=====================================
Split architecture:
- sandbox: path traversal guards, root enforcement, lock reaping
- archival: content-addressed disk storage and poison file isolation
- persist: atomic database transaction and document version lineage
- pipeline: closed-loop 8-state machine coordination
"""

from .sandbox import (
    ALLOWED_EXT,
    DENIED_SYSTEM_ROOTS,
    validate_safe_path,
    reap_stale_locks,
    sanitize_filename
)
from .archival import (
    archive_success,
    handle_failure
)
from .persist import (
    cleanup_uncommitted_chunks,
    resolve_document_lineage,
    commit_document
)
from .pipeline import IngestPipeline

__all__ = [
    "IngestPipeline",
    "validate_safe_path",
    "reap_stale_locks",
    "sanitize_filename",
    "ALLOWED_EXT",
    "DENIED_SYSTEM_ROOTS",
    "archive_success",
    "handle_failure",
    "cleanup_uncommitted_chunks",
    "resolve_document_lineage",
    "commit_document"
]
