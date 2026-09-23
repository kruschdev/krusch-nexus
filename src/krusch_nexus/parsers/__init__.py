"""
src/krusch_nexus/parsers/__init__.py
===================================
Public registry facade for KruschNexus document parsers.
"""

from ..models import PageData, ParserResult, ContentBlock, StructuredLocator, WarningCode
from .ocr import OCRPolicy, try_tesseract_ocr
from .pdf import parse_pdf, suppress_running_headers_footers
from .docx import parse_docx
from .eml import parse_eml
from .html import parse_html, extract_html_text
from .tabular import parse_csv
from .registry import (
    compute_file_hash,
    detect_file_mime,
    parse_plain_or_code,
    parse_document,
    PARSER_REGISTRY
)

# Backward-compatible aliases
ParsedPage = PageData
ParsedDocument = ParserResult

__all__ = [
    "parse_document",
    "compute_file_hash",
    "detect_file_mime",
    "OCRPolicy",
    "try_tesseract_ocr",
    "parse_pdf",
    "parse_docx",
    "parse_eml",
    "parse_html",
    "extract_html_text",
    "parse_csv",
    "parse_plain_or_code",
    "suppress_running_headers_footers",
    "PARSER_REGISTRY",
    "ParsedPage",
    "ParsedDocument",
    "PageData",
    "ParserResult",
    "ContentBlock",
    "StructuredLocator",
    "WarningCode"
]
