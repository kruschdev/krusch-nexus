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
    r'^(?:(?:EXHIBIT|(?:PLTF|DEF|GOV|STATE)\s+EX(?:HIBIT)?)\s*(?:#|NO\.?)?\s*[\w\.\-]+'
    r'|FILED\s+(?:IN\s+CLERK\'?S\s+OFFICE|BY\s+COURT)?\s*[\d\/\-]+'
    r'|(?:REC(?:EIV)?(?:ED)?[\s\W_gG&]*FILED|FILED[\s\W_gG&]*REC(?:EIV)?(?:ED)?)'
    r'|(?:(?:REC(?:EIV)?(?:ED)?|FILED|ENTERED|SERVED|MAILED|POSTED)\b[\s&/]*)*(?:(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[\s\.,\-_]*\d{1,2}[\s\.,\-_]*\d{2,4}|\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})'
    r'|(?:(?:REC(?:EIV)?(?:ED)?|FILED|ENTERED)[\s&/]*)+)$',
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
        suppressed_set = set()

        for idx, line in enumerate(lines):
            l_low = line.lower()
            l_strip = line.strip()
            is_bates = bool(BATES_REGEX.search(l_strip)) and len(l_strip) <= 30
            is_exhibit = bool(EXHIBIT_STAMP_REGEX.search(l_strip)) and len(l_strip) <= 50
            is_fax = bool(FAX_STAMP_REGEX.search(l_strip)) and len(l_strip) <= 120
            is_conf = bool(CONFIDENTIAL_REGEX.search(l_low)) and len(l_strip) <= 80
            is_pacer = bool(PACER_DOCKET_REGEX.search(line)) and len(l_strip) <= 80
            is_pagenum = bool(PAGE_NUM_REGEX.search(l_low)) and len(l_strip) <= 20

            is_top = (idx < 4) and (l_low in suppress_top or is_conf or is_pacer)
            is_bottom = (idx >= len(lines) - 2) and (l_low in suppress_bottom or is_pagenum)

            if is_bates:
                p.blocks.append(ContentBlock(text=line, block_type="bates_stamp"))
                suppressed_set.add(l_strip)
            elif is_exhibit:
                p.blocks.append(ContentBlock(text=line, block_type="exhibit_stamp"))
                suppressed_set.add(l_strip)
            elif is_fax:
                p.blocks.append(ContentBlock(text=line, block_type="fax_stamp"))
                suppressed_set.add(l_strip)
            elif is_top or is_bottom:
                p.blocks.append(ContentBlock(text=line, block_type="header_footer"))
                suppressed_set.add(l_strip)
            else:
                cleaned_lines.append(line)

        if suppressed_set:
            raw_lines = (p.text or "").splitlines()
            rebuilt = [ln for ln in raw_lines if ln.strip() not in suppressed_set]
            new_text = "\n".join(rebuilt).strip()
            p.text = new_text
            p.char_count = len(new_text)
        else:
            p.char_count = len(p.text or "")

    return pages


def _extract_poppler_blocks(
    pdftotext_bin: str,
    file_path: str,
    page_num: int,
    timeout: float
) -> Tuple[str, List[ContentBlock], List[Dict[str, Any]]]:
    """
    Extract digital text with bounding boxes using pdftotext -tsv.
    Detects two-column layouts and preserves natural reading order.
    Extracts structured multi-column table grids (both delimited with '|' and borderless
    gutter-aligned financial spreadsheets) with sub-line cell-level bounding boxes.
    Falls back to pdftotext -layout if TSV mode is unavailable or produces no lines.
    """
    blocks: List[ContentBlock] = []
    tables: List[Dict[str, Any]] = []
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
        line_boxes: Dict[Tuple[int, int, int], Tuple[float, float, float, float]] = {}
        line_words: Dict[Tuple[int, int, int], List[Dict[str, Any]]] = {}

        for row in lines[1:]:
            parts = row.split('\t')
            if len(parts) >= 12:
                try:
                    level = int(parts[0])
                    block_num = int(parts[3])
                    par_num = int(parts[2])
                    line_num = int(parts[4])
                    key = (block_num, par_num, line_num)
                    if level == 4:
                        left = float(parts[6])
                        top = float(parts[7])
                        width = float(parts[8])
                        height = float(parts[9])
                        line_boxes[key] = (left, top, width, height)
                    elif level == 5:
                        w = parts[11].strip()
                        if w:
                            w_left = float(parts[6])
                            w_top = float(parts[7])
                            w_width = float(parts[8])
                            w_height = float(parts[9])
                            line_words.setdefault(key, []).append({
                                "text": w,
                                "bbox": [w_left, w_top, w_width, w_height]
                            })
                except (ValueError, IndexError):
                    continue

        line_records = []
        for key in sorted(line_boxes.keys()):
            words = line_words.get(key, [])
            if words:
                text_val = " ".join(w["text"] for w in words)
                left, top, width, height = line_boxes[key]
                line_records.append({
                    "left": left,
                    "top": top,
                    "width": width,
                    "height": height,
                    "text": text_val,
                    "words": words
                })

        if line_records:
            # Check for two-column prose layout vs multi-column table
            lefts = [r["left"] for r in line_records]
            min_l, max_l = min(lefts), max(lefts)
            col_split = (min_l + max_l) / 2.0
            left_col = [r for r in line_records if (r["left"] + r["width"] / 2.0) < col_split]
            right_col = [r for r in line_records if (r["left"] + r["width"] / 2.0) >= col_split]

            # In a multi-column table, multiple records in left_col and right_col share exact vertical baselines.
            # In genuine two-column prose, independent paragraphs do not synchronize baselines across columns.
            shared_baselines = 0
            for l_rec in left_col:
                if any(abs(l_rec["top"] - r_rec["top"]) <= 2.5 for r_rec in right_col):
                    shared_baselines += 1

            is_two_column = (
                len(left_col) >= 3 and len(right_col) >= 3
                and (col_split - min_l > 80)
                and (shared_baselines < 2)
            )

            if is_two_column:
                # Column 1 top-to-bottom, followed by Column 2 top-to-bottom
                left_col.sort(key=lambda r: r["top"])
                right_col.sort(key=lambda r: r["top"])
                ordered_records = left_col + right_col
                raw_baselines = [[r] for r in ordered_records]
            else:
                # Group records sharing a vertical baseline (within 2.5 pt) into rows
                sorted_by_top = sorted(line_records, key=lambda r: r["top"])
                raw_baselines = []
                for r in sorted_by_top:
                    placed = False
                    for b in raw_baselines:
                        if abs(b[0]["top"] - r["top"]) <= 2.5:
                            b.append(r)
                            placed = True
                            break
                    if not placed:
                        raw_baselines.append([r])
                for b in raw_baselines:
                    b.sort(key=lambda r: r["left"])

            text_pieces = []
            prev_rec = None
            current_table_rows = []

            def flush_table():
                nonlocal current_table_rows, tables
                if len(current_table_rows) >= 2:
                    t_idx = len(tables) + 1
                    all_bboxes = [r["bbox"] for r in current_table_rows]
                    t_left = min(b[0] for b in all_bboxes)
                    t_top = min(b[1] for b in all_bboxes)
                    t_right = max(b[0] + b[2] for b in all_bboxes)
                    t_bottom = max(b[1] + b[3] for b in all_bboxes)
                    t_bbox = [round(t_left, 2), round(t_top, 2), round(t_right - t_left, 2), round(t_bottom - t_top, 2)]

                    md_lines = []
                    for r in current_table_rows:
                        row_cells_text = [c["text"] for c in r["cells"]]
                        md_lines.append("| " + " | ".join(row_cells_text) + " |")
                    if len(md_lines) > 1:
                        cols_count = max(len(r["cells"]) for r in current_table_rows)
                        sep_line = "| " + " | ".join(["---"] * cols_count) + " |"
                        md_lines.insert(1, sep_line)

                    tables.append({
                        "table_id": f"Table {t_idx}",
                        "bbox": t_bbox,
                        "num_rows": len(current_table_rows),
                        "num_cols": max(len(r["cells"]) for r in current_table_rows),
                        "markdown": "\n".join(md_lines),
                        "rows": current_table_rows
                    })
                current_table_rows = []

            for b in raw_baselines:
                if len(b) > 1:
                    # Multi-segment borderless table row
                    cells = []
                    for seg in b:
                        cells.append({
                            "col_idx": len(cells),
                            "text": seg["text"],
                            "bbox": [round(seg["left"], 2), round(seg["top"], 2), round(seg["width"], 2), round(seg["height"], 2)]
                        })
                    r_left = min(c["bbox"][0] for c in cells)
                    r_top = min(c["bbox"][1] for c in cells)
                    r_right = max(c["bbox"][0] + c["bbox"][2] for c in cells)
                    r_bottom = max(c["bbox"][1] + c["bbox"][3] for c in cells)
                    r_bbox = [round(r_left, 2), round(r_top, 2), round(r_right - r_left, 2), round(r_bottom - r_top, 2)]
                    row_text = " | ".join(c["text"] for c in cells)

                    row_record = {
                        "row_idx": len(current_table_rows),
                        "text": row_text,
                        "bbox": r_bbox,
                        "cells": cells
                    }
                    current_table_rows.append(row_record)

                    blocks.append(ContentBlock(
                        text=row_text,
                        block_type="table_row",
                        bbox=r_bbox,
                        extra={"cells": cells}
                    ))

                    rec_for_text = {"text": row_text, "left": r_left, "top": r_top, "height": r_bottom - r_top}
                    if prev_rec is None:
                        text_pieces.append(row_text)
                    else:
                        gap = rec_for_text["top"] - (prev_rec["top"] + prev_rec["height"])
                        is_para_break = gap > 1.2 * prev_rec["height"] or gap < -prev_rec["height"]
                        sep = "\n\n" if is_para_break else "\n"
                        text_pieces.append(sep + row_text)
                    prev_rec = rec_for_text

                else:
                    rec = b[0]
                    # Check if internal pipe delimiter exists
                    if "|" in rec["text"]:
                        cell_words = []
                        cur = []
                        for w in rec["words"]:
                            if w["text"] == "|":
                                if cur:
                                    cell_words.append(cur)
                                    cur = []
                            else:
                                cur.append(w)
                        if cur:
                            cell_words.append(cur)

                        cells = []
                        for idx, cw in enumerate(cell_words):
                            c_left = min(x["bbox"][0] for x in cw)
                            c_top = min(x["bbox"][1] for x in cw)
                            c_right = max(x["bbox"][0] + x["bbox"][2] for x in cw)
                            c_bottom = max(x["bbox"][1] + x["bbox"][3] for x in cw)
                            cells.append({
                                "col_idx": idx,
                                "text": " ".join(x["text"] for x in cw),
                                "bbox": [round(c_left, 2), round(c_top, 2), round(c_right - c_left, 2), round(c_bottom - c_top, 2)]
                            })

                        r_bbox = [round(rec["left"], 2), round(rec["top"], 2), round(rec["width"], 2), round(rec["height"], 2)]
                        row_record = {
                            "row_idx": len(current_table_rows),
                            "text": rec["text"],
                            "bbox": r_bbox,
                            "cells": cells
                        }
                        current_table_rows.append(row_record)

                        blocks.append(ContentBlock(
                            text=rec["text"],
                            block_type="table_row",
                            bbox=r_bbox,
                            extra={"cells": cells}
                        ))

                        if prev_rec is None:
                            text_pieces.append(rec["text"])
                        else:
                            gap = rec["top"] - (prev_rec["top"] + prev_rec["height"])
                            is_para_break = gap > 1.2 * prev_rec["height"] or gap < -prev_rec["height"]
                            sep = "\n\n" if is_para_break else "\n"
                            text_pieces.append(sep + rec["text"])
                        prev_rec = rec
                    else:
                        # Regular text block - flush any accumulated table
                        flush_table()
                        r_bbox = [round(rec["left"], 2), round(rec["top"], 2), round(rec["width"], 2), round(rec["height"], 2)]
                        blocks.append(ContentBlock(
                            text=rec["text"],
                            block_type="paragraph",
                            bbox=r_bbox
                        ))

                        if prev_rec is None:
                            text_pieces.append(rec["text"])
                        else:
                            gap = rec["top"] - (prev_rec["top"] + prev_rec["height"])
                            is_para_break = gap > 1.2 * prev_rec["height"] or gap < -prev_rec["height"]
                            sep = "\n\n" if is_para_break else "\n"
                            text_pieces.append(sep + rec["text"])
                        prev_rec = rec

            flush_table()
            combined_text = "".join(text_pieces)
            return combined_text, blocks, tables

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

    return digital_text, blocks, tables


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
        digital_text, page_blocks, page_tables = _extract_poppler_blocks(pdftotext_bin, file_path, page_num, timeout)

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
                page_tables = []
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
            tables=page_tables,
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
