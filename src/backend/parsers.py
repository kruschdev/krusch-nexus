"""
Backward-compatibility shim for src.backend.parsers.
Deprecated: Use 'from krusch_nexus.parsers import ...' instead.
"""
from krusch_nexus.parsers import (
    Document,
    ParsedPage,
    ParsedDocument,
    compute_file_hash,
    parse_pdf,
    parse_docx,
    parse_eml,
    parse_html,
    parse_plain_or_code,
    parse_document
)

__all__ = [
    "Document",
    "ParsedPage",
    "ParsedDocument",
    "compute_file_hash",
    "parse_pdf",
    "parse_docx",
    "parse_eml",
    "parse_html",
    "parse_plain_or_code",
    "parse_document"
]
