"""
Backward-compatibility shim for src.backend.embeddings.
Deprecated: Use 'from krusch_nexus.embeddings import ...' instead.
"""
from krusch_nexus.embeddings import (
    hash_text,
    get_embedding,
    get_embeddings_batch
)

__all__ = ["hash_text", "get_embedding", "get_embeddings_batch"]
