"""
Backward-compatibility shim for src.backend.chunking.
Deprecated: Use 'from krusch_nexus.chunking import ...' instead.
"""
from krusch_nexus.chunking import (
    Chunk,
    compute_chunk_hash,
    detect_header_candidate,
    chunk_document_pages,
    chunk_llama_documents,
    deduplicate_chunks
)

__all__ = [
    "Chunk",
    "compute_chunk_hash",
    "detect_header_candidate",
    "chunk_document_pages",
    "chunk_llama_documents",
    "deduplicate_chunks"
]
