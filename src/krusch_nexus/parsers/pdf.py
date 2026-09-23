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
from typing import List, Dict, Any, Optional, Tuple

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


BATES_REGEX = re.compile(r'^(?:[A-Z]{2,12}[-_\s]*\d{4,12}|[A-Z]{2,12}\s*#\s*\d{4,12})$', re.IGNORECASE)
EXHIBIT_STAMP_REGEX = re.compile(
    r'^(?:(?:EXHIBIT|(?:PLTF|DEF|GOV|STATE)\s+EX(?:HIBIT)?)\s*(?:#|NO\.?)?\s*[\w\.\-]+|FILED\s+(?:IN\s+CLERK\'?S\s+OFFICE|BY\s+COURT)?\s*[\d\/\-]+)$',
    re.IGNORECASE
)
FAX_STAMP_REGEX = re.compile(r'^(?:(?:TRANSMISSION|FAX|SENT|RCVD)\s*(?:OK|RECORD|REPORT|BY)?|\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?\s*(?:FAX|PAGE)?)\b', re.IGNORECASE)
PAGE_NUM_REGEX = re.compile(r'^(?:page\s+\d+(?:\s+of\s+\d+)?|\d+\s*[\/\-]\s*\d+|-\s*\d+\s*-|\d+)$', re.IGNORECASE)
CONFIDENTIAL_REGEX = re.compile(r'^(?:confidential|privileged|all rights reserved|attorney-client privilege)\b', re.IGNORECASE)
PACER_DOCKET_REGEX = re.compile(r'^(?:Case\s+[0-9]+:[0-9]{2}-[a-z]{2,4}-[0-9]+|Doc(?:ument)?\.?\s+\d+.*Filed|Page\s+\d+\s+of\s+\d+.*PageID)\b', re.IGNORECASE)


def suppress_running_headers_footers(pages: List[PageData]) -> List[PageData]:
    """
    Detect and suppress repeating running headers, footers, Bates stamps, and exhibit labels.
    Suppressed lines are removed from the main page text so they don't pollute embeddings,
    while being preserved in page.blocks with appropriate noise block types.
    """
    top_candidates: List[str] = []
    bottom_candidates: List[str] = []
    page_lines_map: Dict[int, List[str]] = {}

    for i, p in enumerate(pages):
        raw = p.text or p.digital_text or p.ocr_text or ""
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        page_lines_map[i] = lines
        if lines:
            top_candidates.append(lines[0].lower())
            if len(lines) >= 2:
                top_candidates.append(lines[1].lower())
            bottom_candidates.append(lines[-1].lower())
            if len(lines) >= 2:
                bottom_candidates.append(lines[-2].lower())

    top_counts = Counter(top_candidates)
    bottom_counts = Counter(bottom_candidates)
    threshold = max(2, len(pages) // 2) if len(pages) >= 2 else 999

    suppress_top = {line for line, cnt in top_counts.items() if cnt >= threshold}
    suppress_bottom = {line for line, cnt in bottom_counts.items() if cnt >= threshold}

    for i, p in enumerate(pages):
        lines = page_lines_map[i]
        if not lines:
            continue
        cleaned_lines = []

        for idx, line in enumerate(lines):
            l_low = line.lower()
            l_strip = line.strip()
            is_bates = bool(BATES_REGEX.search(l_strip)) and len(l_strip) <= 30
            is_exhibit = bool(EXHIBIT_STAMP_REGEX.search(l_strip)) and len(l_strip) <= 45
            is_fax = bool(FAX_STAMP_REGEX.search(l_strip)) and len(l_strip) <= 50
            is_conf = bool(CONFIDENTIAL_REGEX.search(l_low)) and len(l_strip) <= 60
            is_pacer = bool(PACER_DOCKET_REGEX.search(line)) and len(l_strip) <= 80
            is_pagenum = bool(PAGE_NUM_REGEX.search(l_low)) and len(l_strip) <= 20

            is_top = (idx < 2) and (l_low in suppress_top or is_conf or is_pacer)
            is_bottom = (idx >= len(lines) - 2) and (l_low in suppress_bottom or is_pagenum)

            if is_bates:
                p.blocks.append(ContentBlock(text=line, block_type="bates_stamp"))
            elif is_exhibit:
                p.blocks.append(ContentBlock(text=line, block_type="exhibit_stamp"))
            elif is_fax:
                p.blocks.append(ContentBlock(text=line, block_type="fax_stamp"))
            elif is_top or is_bottom:
                p.blocks.append(ContentBlock(text=line, block_type="header_footer"))
            else:
                cleaned_lines.append(line)

        new_text = "\n".join(cleaned_lines)
        p.text = new_text
        p.char_count = len(new_text)

    return pages


def _extract_poppler_blocks(
    pdftotext_bin: str,
    file_path: str,
    page_num: int,
    timeout: float
) -> Tuple[str, List[ContentBlock]]:
    """
    Extract digital text with bounding boxes using pdftotext -tsv.
    Detects two-column layouts by x-coordinate distribution and preserves column reading order.
    Falls back to pdftotext -layout if TSV mode is unavailable or produces no lines.
    """
    blocks: List[ContentBlock] = []
    tsv_proc = None
    try:
        tsv_proc = subprocess.run(
            [pdftotext_bin, "-tsv", "-f", str(page_num), "-l", str(page_num), file_path, "-"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False
        )
    except Exception:
        pass

    if tsv_proc and tsv_proc.returncode == 0 and tsv_proc.stdout.strip():
        lines = tsv_proc.stdout.splitlines()
        line_boxes: Dict[Tuple[int, int], Tuple[float, float, float, float]] = {}
        line_words: Dict[Tuple[int, int], List[str]] = {}

        for row in lines[1:]:
            parts = row.split('\t')
            if len(parts) >= 12:
                try:
                    level = int(parts[0])
                    par_num = int(parts[2])
                    line_num = int(parts[4])
                    key = (par_num, line_num)
                    if level == 4:
                        left = float(parts[6])
                        top = float(parts[7])
                        width = float(parts[8])
                        height = float(parts[9])
                        line_boxes[key] = (left, top, width, height)
                    elif level == 5:
                        w = parts[11].strip()
                        if w:
                            line_words.setdefault(key, []).append(w)
                except (ValueError, IndexError):
                    continue

        line_records = []
        for key in sorted(line_boxes.keys()):
            words = line_words.get(key, [])
            if words:
                text_val = " ".join(words)
                left, top, width, height = line_boxes[key]
                line_records.append({
                    "left": left,
                    "top": top,
                    "width": width,
                    "height": height,
                    "text": text_val
                })

        if line_records:
            # Check for two-column layout: do lines separate into two distinct x clusters?
            lefts = [r["left"] for r in line_records]
            min_l, max_l = min(lefts), max(lefts)
            col_split = (min_l + max_l) / 2.0
            left_col = [r for r in line_records if (r["left"] + r["width"] / 2.0) < col_split]
            right_col = [r for r in line_records if (r["left"] + r["width"] / 2.0) >= col_split]

            # Detect two-column if both sides have significant content and don't strongly overlap
            is_two_column = len(left_col) >= 3 and len(right_col) >= 3 and (col_split - min_l > 80)
            if is_two_column:
                # Column 1 top-to-bottom, followed by Column 2 top-to-bottom
                left_col.sort(key=lambda r: r["top"])
                right_col.sort(key=lambda r: r["top"])
                ordered_records = left_col + right_col
            else:
                ordered_records = sorted(line_records, key=lambda r: r["top"])

            for rec in ordered_records:
                blocks.append(ContentBlock(
                    text=rec["text"],
                    block_type="paragraph",
                    bbox=[round(rec["left"], 2), round(rec["top"], 2), round(rec["width"], 2), round(rec["height"], 2)]
                ))
            combined_text = "\n".join(b.text for b in blocks)
            return combined_text, blocks

    # Fallback to standard -layout
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
            for para in [p.strip() for p in digital_text.split('\n\n') if p.strip()]:
                blocks.append(ContentBlock(text=para, block_type="paragraph"))
    except Exception:
        pass

    return digital_text, blocks


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
    Parse PDF page-by-page using Poppler 'pdftotext -f N -l N' with TSV bounding boxes.
    Enforces encrypted PDF detection, quality-bounded OCR fallback governed by OCRPolicy,
    separate digital_text and ocr_text storage, multi-column order detection, and noise suppression.
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
        digital_text, page_blocks = _extract_poppler_blocks(pdftotext_bin, file_path, page_num, timeout)

        ocr_applied = False
        ocr_text: Optional[str] = None
        ocr_confidence: Optional[float] = None

        # Selective OCR: only trigger if digital text printable characters < threshold OR image streams detected with low text
        sparse_text = len(digital_text) < policy.min_printable_chars
        image_stream_low_text = has_image_streams and len(digital_text) < 100
        should_ocr = sparse_text or image_stream_low_text

        page_extra: Dict[str, Any] = {}
        if should_ocr:
            trigger_reason = "sparse_text" if sparse_text else "image_xobject"
            page_extra["ocr_trigger_reason"] = trigger_reason
            logger.info(f"Page {page_num} of '{filename}': OCR triggered due to reason='{trigger_reason}' (digital_chars={len(digital_text)})")

            ocr_res = try_tesseract_ocr(
                file_path,
                page_num,
                policy=policy
            )
            candidate_ocr, conf, ocr_blocks = ocr_res
            quarantine_ptr = getattr(ocr_res, "image_path", None)
            if quarantine_ptr:
                page_extra["page_image_path"] = quarantine_ptr

            if candidate_ocr and len(candidate_ocr) > max(len(digital_text), 15):
                ocr_applied = True
                ocr_text = candidate_ocr
                ocr_confidence = conf
                page_blocks = ocr_blocks
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
            char_count=len(chosen_text),
            extra=page_extra
        ))

    # Suppress repeating running headers, footers, Bates stamps, and exhibit labels
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
