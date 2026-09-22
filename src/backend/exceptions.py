"""
KruschNexus Typed Exceptions
============================
Hierarchical exceptions for document ingestion, parsing, retrieval,
and workspace isolation.
"""


class NexusError(Exception):
    """Base class for all KruschNexus exceptions."""
    pass


class ParseError(NexusError):
    """Raised when parsing or extracting a document fails."""
    pass


class DuplicateDocument(NexusError):
    """Raised or flagged when an identical file hash has already been ingested."""
    pass


class WorkspaceNotFound(NexusError):
    """Raised when a referenced workspace does not exist."""
    pass


class EmbeddingUnavailable(NexusError):
    """Raised when the vector embedding service (Ollama) cannot be reached or fails."""
    pass


class FileOversizedError(NexusError):
    """Raised when a document exceeds the configured maximum file size limit."""
    pass
