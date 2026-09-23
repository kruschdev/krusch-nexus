"""
src/krusch_nexus/parsers/pdf.py
===============================
Poppler-based page-at-a-time PDF parser with encrypted detection and selective OCR.
"""

import os
import re
import shutil
import logging
import subprocess
from collections import Counter
from typing import List, Dict, Any, Optional

from ..models import PageData, ParserResult, ContentBlock, StructuredLocator, WarningCode
from ..exceptions import EncryptedPdfError, ParseError
from .ocr import OCRPolicy, try_tesseract_ocr, get_system_tool_versions

logger = logging.getLogger("krusch_nexus.parsers.pdf")


def _get_pdf_info(file_path: str, timeout: float = 15.0) -> Dict[str, Any]:
    """Inspect PDF metadata using pdfinfo to check encryption and exact page count."""
    pdfinfo_bin = shutil.which("pdfinfo")
    info = {"pages": 1, "encrypted": False}
    if not pdfinfo_bin:
        return info

    try:
        proc = subprocess.run(
            [pdfinfo_bin, file_path],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False
        )
        combined_output = (proc.stdout or "") + " " + (proc.stderr or "")
        if "incorrect password" in combined_output.lower() or "password required" in combined_output.lower():
            info["encrypted"] = True
            return info

        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if line.startswith("Pages:"):
                    parts = line.split(":", 1)
                    if len(parts) > 1:
                        try:
                            info["pages"] = int(parts[1].strip())
                        except ValueError:
                            pass
                elif line.startswith("Encrypted:"):
                    val = line.split(":", 1)[1].strip().lower()
                    if val.startswith("yes"):
                        info["encrypted"] = True
    except Exception as e:
        logger.warning(f"pdfinfo check failed: {e}")

    # Fallback raw inspection for encrypted PDF trailer / dictionary
    if not info["encrypted"]:
        try:
            with open(file_path, "rb") as f:
                header_trailer = f.read(4096)
                f.seek(max(0, os.path.getsize(file_path) - 4096))
                header_trailer += f.read(4096)
                if b"/Encrypt" in header_trailer:
                    info["encrypted"] = True
        except Exception:
            pass

    return info


def _pdf_contains_images(file_path: str) -> bool:
    """Fast check whether PDF raw stream contains XObject image definitions."""
    try:
        with open(file_path, "rb") as f:
            content = f.read(2_000_000)  # Check first 2MB
            if b"/Image" in content or b"/XObject" in content:
                return True
    except Exception:
        pass
    return False


def suppress_running_headers_footers(pages: List[PageData]) -> List[PageData]:
    """
    Detect and suppress repeating running headers and footers across multi-page documents.
    Suppressed lines are removed from the main page text so they don't pollute embeddings,
    while being preserved in page.blocks with block_type="header_footer".
    """
    if len(pages) < 2:
        return pages

    page_num_regex = re.compile(r'^(?:page\s+\d+(?:\s+of\s+\d+)?|\d+\s*/\s*\d+|-\s*\d+\s*-|\d+)$', re.IGNORECASE)
    confidential_regex = re.compile(r'^(?:confidential|privileged|all rights reserved|attorney-client privilege)\b', re.IGNORECASE)

    top_candidates: List[str] = []
    bottom_candidates: List[str] = []
    page_lines_map: List[List[str]] = []

    for p in pages:
        lines = [line.strip() for line in p.text.splitlines() if line.strip()]
        page_lines_map.append(lines)
        if len(lines) >= 1:
            top_candidates.append(lines[0].lower())
            if len(lines) >= 2:
                top_candidates.append(lines[1].lower())
            bottom_candidates.append(lines[-1].lower())
            if len(lines) >= 2:
                bottom_candidates.append(lines[-2].lower())

    top_counts = Counter(top_candidates)
    bottom_counts = Counter(bottom_candidates)
    threshold = max(2, len(pages) // 2)

    suppress_top = {line for line, cnt in top_counts.items() if cnt >= threshold or confidential_regex.search(line)}
    suppress_bottom = {line for line, cnt in bottom_counts.items() if cnt >= threshold or page_num_regex.search(line)}

    for i, p in enumerate(pages):
        lines = page_lines_map[i]
        if not lines:
            continue
        cleaned_lines = []

        for idx, line in enumerate(lines):
            l_low = line.lower()
            is_top = (idx < 2) and (l_low in suppress_top or confidential_regex.search(l_low))
            is_bottom = (idx >= len(lines) - 2) and (l_low in suppress_bottom or page_num_regex.search(l_low))

            if is_top or is_bottom:
                p.blocks.append(ContentBlock(text=line, block_type="header_footer"))
            else:
                cleaned_lines.append(line)

        new_text = "\n".join(cleaned_lines)
        p.text = new_text
        p.char_count = len(new_text)

    return pages


def parse_pdf(
    file_path: str,
    filename: str,
    policy: Optional[OCRPolicy] = None,
    ocr_threshold: Optional[int] = None,
    ocr_dpi: Optional[int] = None,
    ocr_lang: Optional[str] = None,
    timeout: float = 30.0,
    file_hash: Optional[str] = None,
    detected_mime: Optional[str] = None
) -> ParserResult:
    """
    Parse PDF page-by-page using Poppler 'pdftotext -f N -l N -layout'.
    Enforces encrypted PDF detection, quality-bounded OCR fallback governed by OCRPolicy,
    separate digital_text and ocr_text storage, and running header/footer suppression.
    """
    if policy is None:
        policy = OCRPolicy()
        if ocr_threshold is not None:
            policy.min_printable_chars = ocr_threshold
        if ocr_dpi is not None:
            policy.dpi = ocr_dpi
        if ocr_lang is not None:
            policy.language = ocr_lang

    info = _get_pdf_info(file_path, timeout=timeout)
    if info.get("encrypted"):
        raise EncryptedPdfError(f"PDF document '{filename}' is encrypted and cannot be parsed.")

    total_pages = info.get("pages", 1)
    pdftotext_bin = shutil.which("pdftotext")
    if not pdftotext_bin:
        raise ParseError("Required Poppler binary 'pdftotext' is missing from the system.")

    pages_data: List[PageData] = []
    has_image_streams = _pdf_contains_images(file_path)
    warnings: List[str] = []

    for page_num in range(1, total_pages + 1):
        digital_text = ""
        try:
            proc = subprocess.run(
                [pdftotext_bin, "-layout", "-f", str(page_num), "-l", str(page_num), file_path, "-"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False
            )
            if proc.returncode == 0:
                digital_text = proc.stdout.strip()
        except Exception as e:
            warnings.append(f"pdftotext failed on page {page_num}: {e}")

        ocr_applied = False
        ocr_text: Optional[str] = None
        ocr_confidence: Optional[float] = None
        page_blocks: List[ContentBlock] = []

        # Selective OCR: only trigger if digital text printable characters < threshold OR image streams detected with low text
        # If page already has rich digital text (>= min_printable_chars), we preserve digital text and avoid unnecessary OCR!
        should_ocr = len(digital_text) < policy.min_printable_chars or (has_image_streams and len(digital_text) < 100)

        if should_ocr:
            candidate_ocr, conf, blocks = try_tesseract_ocr(
                file_path,
                page_num,
                policy=policy
            )
            if candidate_ocr and len(candidate_ocr) > max(len(digital_text), 15):
                ocr_applied = True
                ocr_text = candidate_ocr
                ocr_confidence = conf
                page_blocks = blocks
                logger.info(f"High-res OCR applied to page {page_num} of '{filename}' ({len(candidate_ocr)} chars, conf: {conf})")
            elif conf is not None and conf < policy.confidence_floor:
                warnings.append(WarningCode.LOW_OCR_CONFIDENCE.value)
                logger.warning(f"Page {page_num} quarantined due to low OCR confidence: {conf:.2f} < {policy.confidence_floor:.2f}")

        # Invariant: Never overwrite digital_text with ocr_text!
        chosen_text = ocr_text if (ocr_applied and ocr_text) else digital_text
        if not chosen_text:
            if ocr_applied:
                warnings.append(WarningCode.OCR_EMPTY_PAGE.value)
                chosen_text = f"[Scanned page {page_num} - image text pending]"
            elif WarningCode.LOW_OCR_CONFIDENCE.value in warnings:
                chosen_text = f"[Scanned page {page_num} - low OCR confidence quarantined]"
            else:
                chosen_text = ""

        if not ocr_applied and digital_text:
            for para in [p.strip() for p in digital_text.split('\n\n') if p.strip()]:
                page_blocks.append(ContentBlock(text=para, block_type="paragraph"))

        pages_data.append(PageData(
            index=page_num,
            locator=f"Page {page_num}",
            structured_locator=StructuredLocator(kind="page", page=page_num, path=[f"Page {page_num}"], formatted=f"Page {page_num}"),
            text=chosen_text,
            digital_text=digital_text,
            ocr_text=ocr_text,
            blocks=page_blocks,
            has_images=has_image_streams,
            ocr_applied=ocr_applied,
            confidence=ocr_confidence,
            char_count=len(chosen_text)
        ))

    # Suppress repeating running headers and footers across pages
    pages_data = suppress_running_headers_footers(pages_data)

    return ParserResult(
        filename=filename,
        mime="application/pdf",
        detected_mime=detected_mime or "application/pdf",
        file_hash=file_hash or "",
        parser_name="pdf-poppler",
        parser_version="pdf-poppler@2.0",
        tool_versions=get_system_tool_versions(),
        pages=pages_data,
        warnings=warnings
    )
