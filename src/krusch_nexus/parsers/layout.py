"""
src/krusch_nexus/parsers/layout.py
==================================
Optional local neural layout parser backend adapter (Docling / Marker).
Maintains strict contract parity: emits the exact same PageData, ContentBlock,
and StructuredLocator models as the default Poppler + Tesseract engine.

Enables advanced multi-column reading flow, embedded table cell extraction,
and visual bounding box segmentation when neural layout libraries are installed.
Falls back seamlessly to Poppler + Tesseract for zero-GPU air-gapped environments.
"""

import logging
from typing import Optional, List, Dict, Any

from ..models import PageData, ParserResult, StructuredLocator
from .ocr import OCRPolicy
from .pdf import parse_pdf

logger = logging.getLogger("krusch_nexus.parsers.layout")


def is_layout_backend_available(backend: str = "docling") -> bool:
    """Check whether the requested local neural layout engine is installed."""
    b_clean = (backend or "").lower().strip()
    if b_clean == "docling":
        try:
            import docling  # noqa: F401
            return True
        except ImportError:
            return False
    elif b_clean == "marker":
        try:
            import marker  # noqa: F401
            return True
        except ImportError:
            return False
    return False


def parse_with_layout_backend(
    file_path: str,
    filename: str,
    backend: str = "docling",
    policy: Optional[OCRPolicy] = None,
    ocr_threshold: Optional[int] = None,
    ocr_dpi: Optional[int] = None,
    ocr_lang: Optional[str] = None,
    timeout: float = 60.0,
    file_hash: Optional[str] = None,
    detected_mime: Optional[str] = None
) -> ParserResult:
    """
    Parse PDF using an optional local layout backend (Docling or Marker).
    If the requested backend is not installed in the Python environment,
    falls back cleanly to the air-gapped Poppler + Tesseract engine with an audit warning.
    """
    b_clean = (backend or "docling").lower().strip()

    # 1. Check if Docling is available
    if b_clean == "docling" and is_layout_backend_available("docling"):
        try:
            from docling.document_converter import DocumentConverter
            converter = DocumentConverter()
            res = converter.convert(file_path)
            docling_doc = res.document

            pages: List[PageData] = []
            for page_idx, page in enumerate(docling_doc.pages, 1):
                page_text = page.export_to_markdown() if hasattr(page, "export_to_markdown") else str(page)
                struct_loc = StructuredLocator(kind="page", page=page_idx, path=[f"Page {page_idx}"], formatted=f"Page {page_idx}")
                
                # Extract tables
                page_tables: List[Dict[str, Any]] = []
                if hasattr(page, "tables"):
                    for t_idx, tbl in enumerate(page.tables, 1):
                        tbl_md = tbl.export_to_markdown() if hasattr(tbl, "export_to_markdown") else str(tbl)
                        page_tables.append({"table_id": f"Table {t_idx}", "markdown": tbl_md})

                pages.append(PageData(
                    index=page_idx,
                    locator=f"Page {page_idx}",
                    structured_locator=struct_loc,
                    text=page_text,
                    digital_text=page_text,
                    tables=page_tables,
                    char_count=len(page_text)
                ))

            return ParserResult(
                filename=filename,
                mime="application/pdf",
                detected_mime=detected_mime or "application/pdf",
                file_hash=file_hash or "",
                parser_name="pdf-docling",
                parser_version="docling@2.0",
                tool_versions={"layout_engine": "docling"},
                pages=pages
            )
        except Exception as e:
            logger.warning(f"Docling layout parsing failed ({e}); falling back to Poppler")

    # 2. Check if Marker is available
    if b_clean == "marker" and is_layout_backend_available("marker"):
        try:
            from marker.convert import convert_single_pdf
            full_text, images, out_meta = convert_single_pdf(file_path)
            
            # Marker returns single markdown output, split by page markers if present
            raw_pages = full_text.split("\n\n---\n\n")
            pages = []
            for p_idx, p_content in enumerate(raw_pages, 1):
                clean_p = p_content.strip()
                struct_loc = StructuredLocator(kind="page", page=p_idx, path=[f"Page {p_idx}"], formatted=f"Page {p_idx}")
                pages.append(PageData(
                    index=p_idx,
                    locator=f"Page {p_idx}",
                    structured_locator=struct_loc,
                    text=clean_p,
                    digital_text=clean_p,
                    char_count=len(clean_p)
                ))

            return ParserResult(
                filename=filename,
                mime="application/pdf",
                detected_mime=detected_mime or "application/pdf",
                file_hash=file_hash or "",
                parser_name="pdf-marker",
                parser_version="marker@1.0",
                tool_versions={"layout_engine": "marker"},
                pages=pages
            )
        except Exception as e:
            logger.warning(f"Marker layout parsing failed ({e}); falling back to Poppler")

    # 3. Default Air-Gap Fallback: Poppler + Tesseract
    fallback_res = parse_pdf(
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
    if b_clean not in ("poppler", "default"):
        fallback_res.warnings.append(
            f"LAYOUT_BACKEND_UNAVAILABLE: Neural layout backend '{b_clean}' not found in environment; used Poppler default."
        )
    return fallback_res
