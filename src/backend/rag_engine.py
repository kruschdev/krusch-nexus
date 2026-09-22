"""
Backward-compatibility shim for src.backend.rag_engine.
Deprecated: Use 'from krusch_nexus.search import hybrid_search, format_citation' instead.
"""
from krusch_nexus.search import (
    hybrid_search,
    format_citation
)

__all__ = ["hybrid_search", "format_citation"]
