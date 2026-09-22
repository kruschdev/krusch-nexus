"""
Backward-compatibility shim for src.backend.db.
Deprecated: Use 'from krusch_nexus.db import ...' instead.
"""
from krusch_nexus.db import (
    Base,
    Workspace,
    Document,
    DocumentChunk,
    get_engine,
    get_session_factory,
    init_db
)

__all__ = [
    "Base",
    "Workspace",
    "Document",
    "DocumentChunk",
    "get_engine",
    "get_session_factory",
    "init_db"
]
