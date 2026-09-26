"""
KruschNexus Exception Hierarchy
===============================
Standardized exceptions for closed-loop document ingestion, security sandboxing,
workspace isolation, and air-gap policy enforcement.
"""


class NexusError(Exception):
    """Base exception for all KruschNexus operational errors."""
    pass


class AirGapViolationError(NexusError):
    """Raised when an operation attempts non-local network access or unauthorized cloud provider."""
    pass


class PathSandboxError(NexusError):
    """Raised when a file path violates sandbox boundaries or attempts path traversal."""
    pass


class WorkspaceRequiredError(NexusError):
    """Raised when an operation requires an explicit workspace but none or an insecure default was provided."""
    pass


class ConfigurationError(NexusError):
    """Raised when configuration contains insecure defaults or invalid parameters."""
    pass


class ParseError(NexusError):
    """Raised when document parsing fails or returns zero usable content."""
    pass


class DuplicateDocument(NexusError):
    """Raised when document content SHA-256 hash already exists in the target workspace."""
    pass


class WorkspaceNotFound(NexusError):
    """Raised when the specified workspace does not exist in the database."""
    pass


class EmbeddingUnavailable(NexusError):
    """Raised when the local Ollama embedding host cannot be reached or fails to embed."""
    pass


class FileOversizedError(NexusError):
    """Raised when an incoming file exceeds configured byte size or page count limits."""
    pass


# Directive 3 & 4: Explicit Error Classes
class TooLargeError(FileOversizedError):
    """Raised when file exceeds byte size or page limit."""
    pass


class EncryptedPdfError(ParseError):
    """Raised when PDF file is password protected or encrypted and cannot be parsed."""
    pass


class EmptyOcrError(ParseError):
    """Raised when OCR produces empty or non-viable text."""
    pass


class UnsupportedMimeError(ParseError):
    """Raised when file MIME type or extension is unsupported."""
    pass


class CorruptedFileError(ParseError):
    """Raised when file structure is malformed or unreadable."""
    pass


class AuthenticationError(NexusError):
    """Raised when API token or authorization fails."""
    pass


class ModelDimensionDriftError(NexusError):
    """Raised when query vector dimension does not match document embedding dimension."""
    pass


class LegalHoldActiveError(NexusError):
    """Raised when an operation attempts to mutate, reparse, or delete a document or workspace under active legal hold."""
    pass

