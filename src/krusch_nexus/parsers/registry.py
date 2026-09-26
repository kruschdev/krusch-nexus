"""
src/krusch_nexus/parsers/registry.py
====================================
Parser registry, MIME and magic-byte sniffer, and dispatcher with version tracking.
"""

import os
import json
import hashlib
import zipfile
import logging
from typing import Optional, Dict, Any

from ..models import PageData, ParserResult, StructuredLocator
from .ocr import OCRPolicy
from .pdf import parse_pdf
from .docx import parse_docx
from .eml import parse_eml
from .tabular import parse_csv
from .html import parse_html

logger = logging.getLogger("krusch_nexus.parsers.registry")


def compute_file_hash(file_path: str) -> str:
    """Compute SHA-256 hash of a file for exact deduplication."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def detect_file_mime(file_path: str, filename: str) -> str:
    """
    Detect actual MIME type from file header magic bytes and structure,
    rather than trusting the file extension alone.
    """
    try:
        with open(file_path, "rb") as f:
            header = f.read(2048)
    except Exception:
        return "application/octet-stream"

    if header.startswith(b"%PDF-"):
        return "application/pdf"

    if header.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                names = zf.namelist()
                if "word/document.xml" in names:
                    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        except Exception:
            pass
        return "application/zip"

    # Check EML / RFC822
    header_lower = header[:1024].lower()
    if (b"from:" in header_lower and b"subject:" in header_lower) or (b"received:" in header_lower):
        return "message/rfc822"

    # Check HTML
    stripped_head = header.strip().lower()
    if stripped_head.startswith(b"<!doctype html") or stripped_head.startswith(b"<html"):
        return "text/html"

    # Check JSON
    try:
        text_preview = header.decode("utf-8").strip()
        if (text_preview.startswith("{") and text_preview.endswith("}")) or (text_preview.startswith("[") and text_preview.endswith("]")):
            json.loads(text_preview)
            return "application/json"
    except Exception:
        pass

    # Check CSV
    try:
        text_sample = header.decode("utf-8", errors="replace")
        lines = [line.strip() for line in text_sample.splitlines() if line.strip()][:5]
        if len(lines) >= 2 and all("," in line or "\t" in line for line in lines):
            delimiter = "," if lines[0].count(",") >= lines[0].count("\t") else "\t"
            counts = [ln.count(delimiter) for ln in lines]
            if len(set(counts)) == 1 and counts[0] > 0:
                return "text/csv"
    except Exception:
        pass

    # Fallback to extension heuristic if text
    ext = os.path.splitext(filename)[1].lower()
    ext_map = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".eml": "message/rfc822",
        ".msg": "application/vnd.ms-outlook",
        ".html": "text/html",
        ".htm": "text/html",
        ".csv": "text/csv",
        ".json": "application/json",
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".py": "text/x-python",
        ".sql": "text/x-sql",
    }
    return ext_map.get(ext, "text/plain")


def parse_plain_or_code(
    file_path: str,
    filename: str,
    file_hash: Optional[str] = None,
    detected_mime: Optional[str] = None
) -> ParserResult:
    """Parse plain text, Markdown, JSON, or code files with encoding detection."""
    content = ""
    for enc in ["utf-8", "utf-8-sig", "latin-1"]:
        try:
            with open(file_path, "r", encoding=enc) as f:
                content = f.read()
            break
        except UnicodeDecodeError:
            continue

    ext = filename.lower().split('.')[-1] if '.' in filename else ""
    mime = "text/plain"

    if ext == "json":
        mime = "application/json"
        try:
            parsed = json.loads(content)
            content = json.dumps(parsed, indent=2)
        except Exception:
            pass
    elif ext == "md":
        mime = "text/markdown"

    clean_content = content.strip()
    idx = 1 if ext in ["txt", "text"] else None
    loc = f"Page {idx}" if idx is not None else "General"
    struct_loc = StructuredLocator(kind="page" if idx is not None else "heading", page=idx, path=[loc], formatted=loc)
    pages = [PageData(
        index=idx,
        locator=loc,
        structured_locator=struct_loc,
        text=clean_content,
        digital_text=clean_content,
        char_count=len(clean_content)
    )]

    return ParserResult(
        filename=filename,
        mime=mime,
        detected_mime=detected_mime or mime,
        file_hash=file_hash or "",
        parser_name="text-plain",
        parser_version="text-plain@2.0",
        pages=pages
    )


# Canonical Parser Registry mapping MIME/signatures to handlers and versions
PARSER_REGISTRY: Dict[str, Dict[str, Any]] = {
    "application/pdf": {
        "handler": parse_pdf,
        "name": "pdf-poppler",
        "version": "pdf-poppler@2.0"
    },
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {
        "handler": parse_docx,
        "name": "docx-xml",
        "version": "docx-xml@2.0"
    },
    "message/rfc822": {
        "handler": parse_eml,
        "name": "eml-rfc822",
        "version": "eml-rfc822@2.0"
    },
    "text/html": {
        "handler": parse_html,
        "name": "html-stdlib",
        "version": "html-stdlib@2.0"
    },
    "text/csv": {
        "handler": parse_csv,
        "name": "csv-rowgroup",
        "version": "csv-rowgroup@2.0"
    },
    "text/plain": {
        "handler": parse_plain_or_code,
        "name": "text-plain",
        "version": "text-plain@2.0"
    }
}


def parse_document(
    file_path: str,
    filename: str,
    policy: Optional[OCRPolicy] = None,
    ocr_threshold: Optional[int] = None,
    ocr_dpi: Optional[int] = None,
    ocr_lang: Optional[str] = None,
    timeout: float = 30.0
) -> ParserResult:
    """
    Unified entry point for document parsing.
    Dispatches to format-specific parsers based on sniffed MIME and magic bytes,
    writing exact parser versions into the ParserResult contract.
    """
    file_hash = compute_file_hash(file_path)
    detected_mime = detect_file_mime(file_path, filename)

    if detected_mime == "application/pdf":
        res = parse_pdf(
            file_path,
            filename,
            policy=policy,
            ocr_threshold=ocr_threshold,
            ocr_dpi=ocr_dpi,
            ocr_lang=ocr_lang,
            timeout=timeout,
            file_hash=file_hash,
            detected_mime=detected_mime
        )
    elif detected_mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        res = parse_docx(file_path, filename, file_hash=file_hash, detected_mime=detected_mime)
    elif detected_mime == "message/rfc822":
        res = parse_eml(file_path, filename, file_hash=file_hash, detected_mime=detected_mime)
    elif detected_mime == "text/html":
        res = parse_html(file_path, filename, file_hash=file_hash, detected_mime=detected_mime)
    elif detected_mime == "text/csv":
        res = parse_csv(file_path, filename, file_hash=file_hash, detected_mime=detected_mime)
    else:
        res = parse_plain_or_code(file_path, filename, file_hash=file_hash, detected_mime=detected_mime)

    res.file_hash = file_hash
    res.detected_mime = detected_mime

    # Check for MIME sniff vs extension conflict
    ext = os.path.splitext(filename)[1].lower()
    ext_map = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".eml": "message/rfc822",
        ".msg": "application/vnd.ms-outlook",
        ".html": "text/html",
        ".htm": "text/html",
        ".csv": "text/csv",
        ".json": "application/json",
        ".md": "text/markdown",
        ".txt": "text/plain",
    }
    expected_mime = ext_map.get(ext)
    if expected_mime and detected_mime != expected_mime:
        from ..models import WarningCode
        warn_msg = (
            f"{WarningCode.MIME_EXTENSION_MISMATCH.value}: "
            f"Extension '{ext}' suggests '{expected_mime}' but content magic bytes detected '{detected_mime}'."
        )
        logger.warning(warn_msg)
        if warn_msg not in res.warnings:
            res.warnings.append(warn_msg)

    return res
