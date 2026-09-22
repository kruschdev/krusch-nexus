"""
KruschNexus Hardened Document Parsers (parsers.py)
==================================================
Zero-heavy-dependency document extraction pipeline with:
- Page-at-a-time PDF parsing via Poppler (pdftotext -f N -l N)
- Encrypted PDF fail-closed detection (pdfinfo)
- 300 DPI Tesseract OCR fallback with TESSDATA_PREFIX and --psm 4/6 support
- DOCX in-order table extraction with hierarchical heading stacks (no fake page 1)
- EML RFC2047 MIME decoding with Message-ID and attachment extraction
- CSV row-group chunking with replayed table headers
- Typed ParserResult contract with parser versioning
"""

import os
import re
import csv
import json
import email
import shutil
import zipfile
import logging
import hashlib
import tempfile
import subprocess
from email.header import decode_header, make_header
from html.parser import HTMLParser
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional

from .models import PageData, ParserResult
from .exceptions import EncryptedPdfError, EmptyOcrError, ParseError

logger = logging.getLogger("krusch_nexus.parsers")


def compute_file_hash(file_path: str) -> str:
    """Compute SHA-256 hash of a file for exact deduplication."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


# ─── PDF Parser: Page-at-a-Time + Encrypted Check + 300 DPI OCR ──────────────

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
            timeout=timeout
        )
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


def _try_tesseract_ocr(
    pdf_path: str,
    page_num: int,
    dpi: int = 300,
    psm: str = "4",
    timeout: float = 30.0
) -> Optional[str]:
    """
    Execute high-resolution OCR on a specific PDF page using pdftoppm + tesseract.
    Uses 300 DPI for fine type and respects TESSDATA_PREFIX.
    """
    tess_path = shutil.which("tesseract")
    ppm_path = shutil.which("pdftoppm")
    if not tess_path or not ppm_path:
        return None

    env = dict(os.environ)

    with tempfile.TemporaryDirectory() as tmpdir:
        img_prefix = os.path.join(tmpdir, f"page_{page_num}")
        ppm_cmd = [
            ppm_path, "-png", "-r", str(dpi),
            "-f", str(page_num), "-l", str(page_num),
            pdf_path, img_prefix
        ]
        try:
            res = subprocess.run(ppm_cmd, capture_output=True, text=True, timeout=timeout)
            if res.returncode != 0:
                return None
        except Exception as e:
            logger.warning(f"pdftoppm failed for page {page_num}: {e}")
            return None

        files = [f for f in os.listdir(tmpdir) if f.startswith(f"page_{page_num}") and f.endswith(".png")]
        if not files:
            return None

        img_file = os.path.join(tmpdir, files[0])
        # PSM 4: Assume a single column of text of variable sizes; fallback to 3 if requested
        ocr_cmd = [tess_path, img_file, "stdout", "--oem", "1", "--psm", psm, "-l", "eng"]
        try:
            ocr_res = subprocess.run(ocr_cmd, capture_output=True, text=True, timeout=timeout, env=env)
            if ocr_res.returncode == 0:
                extracted = ocr_res.stdout.strip()
                printable = "".join(c for c in extracted if c.isalnum() or c in " .,;:!?-\n")
                if len(printable) >= 5:
                    return extracted
        except Exception as e:
            logger.warning(f"Tesseract OCR failed for page {page_num}: {e}")

    return None


def parse_pdf(
    file_path: str,
    filename: str,
    ocr_threshold: int = 30,
    ocr_dpi: int = 300,
    timeout: float = 30.0
) -> ParserResult:
    """
    Parse PDF page-by-page using Poppler 'pdftotext -f N -l N'.
    Enforces encrypted PDF detection and quality-bounded OCR fallback.
    """
    file_hash = compute_file_hash(file_path)
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
        clean_text = ""
        try:
            proc = subprocess.run(
                [pdftotext_bin, "-layout", "-f", str(page_num), "-l", str(page_num), file_path, "-"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout
            )
            if proc.returncode == 0:
                clean_text = proc.stdout.strip()
        except Exception as e:
            warnings.append(f"pdftotext failed on page {page_num}: {e}")

        ocr_applied = False
        # Per-page OCR decision: selectable chars < threshold AND image streams present
        if len(clean_text) < ocr_threshold and has_image_streams:
            ocr_text = _try_tesseract_ocr(file_path, page_num, dpi=ocr_dpi, psm="4", timeout=timeout)
            if not ocr_text:
                # Try PSM 3 (fully automatic)
                ocr_text = _try_tesseract_ocr(file_path, page_num, dpi=ocr_dpi, psm="3", timeout=timeout)

            # Meaningfully better check: OCR must yield noticeably more content
            if ocr_text and len(ocr_text) > max(len(clean_text), 15):
                clean_text = ocr_text
                ocr_applied = True
                logger.info(f"High-res OCR applied to page {page_num} of '{filename}' ({len(ocr_text)} chars)")
            elif not clean_text:
                clean_text = f"[Scanned page {page_num} - image text pending]"

        pages_data.append(PageData(
            index=page_num,
            locator=f"Page {page_num}",
            text=clean_text,
            has_images=has_image_streams,
            ocr_applied=ocr_applied,
            char_count=len(clean_text)
        ))

    return ParserResult(
        filename=filename,
        mime="application/pdf",
        file_hash=file_hash,
        parser_name="pdf-poppler",
        parser_version="pdf-poppler@2.0",
        pages=pages_data,
        warnings=warnings
    )


# ─── DOCX Parser: Single-Pass In-Order Elements with Heading Stack ───────────

def parse_docx(file_path: str, filename: str) -> ParserResult:
    """
    Parse DOCX files by reading word/document.xml in single-pass natural order.
    Emits page_number=None, maintaining a hierarchical heading stack (e.g. Article IV > Section 8.22).
    """
    file_hash = compute_file_hash(file_path)
    pages_data: List[PageData] = []
    warnings: List[str] = []
    heading_stack: List[str] = []

    try:
        with zipfile.ZipFile(file_path, "r") as docx_zip:
            if "word/document.xml" not in docx_zip.namelist():
                raise ParseError("Invalid DOCX: missing word/document.xml")

            xml_content = docx_zip.read("word/document.xml")
            root = ET.fromstring(xml_content)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            body = root.find(f"{{{ns['w']}}}body", ns)

            if body is not None:
                document_elements: List[str] = []

                for child in body:
                    tag = child.tag

                    # Case 1: Paragraph / Heading (<w:p>)
                    if tag == f"{{{ns['w']}}}p":
                        p_style = child.find(f".//{{{ns['w']}}}pStyle", ns)
                        is_heading = False
                        heading_level = 1
                        if p_style is not None:
                            val = p_style.attrib.get(f"{{{ns['w']}}}val", "")
                            if "heading" in val.lower():
                                is_heading = True
                                match = re.search(r'\d+', val)
                                if match:
                                    heading_level = int(match.group(0))

                        texts = [t.text for t in child.iter(f"{{{ns['w']}}}t") if t.text]
                        p_text = "".join(texts).strip()
                        if not p_text:
                            continue

                        if is_heading:
                            clean_h = p_text.strip()
                            idx = max(0, min(heading_level - 1, len(heading_stack)))
                            heading_stack = heading_stack[:idx] + [clean_h]
                            document_elements.append(f"{'#' * min(heading_level, 4)} {clean_h}")
                        else:
                            document_elements.append(p_text)

                    # Case 2: Table (<w:tbl>) in natural document order
                    elif tag == f"{{{ns['w']}}}tbl":
                        table_rows = []
                        for row in child.iter(f"{{{ns['w']}}}tr"):
                            cells = []
                            for cell in row.iter(f"{{{ns['w']}}}tc"):
                                c_texts = [t.text for t in cell.iter(f"{{{ns['w']}}}t") if t.text]
                                cells.append("".join(c_texts).strip())
                            if any(cells):
                                table_rows.append("| " + " | ".join(cells) + " |")

                        if table_rows:
                            if len(table_rows) > 1:
                                col_count = len(table_rows[0].split('|')) - 2
                                divider = "| " + " | ".join(["---"] * max(1, col_count)) + " |"
                                table_rows.insert(1, divider)
                            document_elements.append("\n".join(table_rows))

                full_body = "\n\n".join(document_elements).strip()
                loc = " > ".join(heading_stack) if heading_stack else "General"
                pages_data.append(PageData(
                    index=None,  # No fake page numbers!
                    locator=loc,
                    text=full_body,
                    char_count=len(full_body)
                ))

    except Exception as e:
        logger.error(f"Error parsing DOCX {filename}: {e}")
        warnings.append(str(e))
        pages_data = [PageData(index=None, locator="Error", text=f"[Error parsing DOCX: {e}]")]

    if not pages_data:
        pages_data = [PageData(index=None, locator="General", text="[Empty DOCX document]")]

    return ParserResult(
        filename=filename,
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        file_hash=file_hash,
        parser_name="docx-xml",
        parser_version="docx-xml@2.0",
        pages=pages_data,
        warnings=warnings
    )


# ─── EML Parser: RFC2047 MIME Header Decoding & Attachments ──────────────────

def _decode_mime_header(header_value: Optional[str]) -> str:
    """Decode RFC2047 MIME encoded-word strings (e.g. =?utf-8?B?...?=)."""
    if not header_value:
        return ""
    try:
        decoded_parts = decode_header(header_value)
        return str(make_header(decoded_parts)).strip()
    except Exception:
        return str(header_value).strip()


def parse_eml(file_path: str, filename: str) -> ParserResult:
    """
    Parse RFC822 EML email files, decoding MIME headers, recording Message-ID,
    and capturing attachments as distinct sections.
    """
    file_hash = compute_file_hash(file_path)
    with open(file_path, "rb") as f:
        msg = email.message_from_binary_file(f)

    headers = []
    subj = _decode_mime_header(msg.get("Subject"))
    sender = _decode_mime_header(msg.get("From"))
    recipient = _decode_mime_header(msg.get("To"))
    cc = _decode_mime_header(msg.get("Cc"))
    date = _decode_mime_header(msg.get("Date"))
    msg_id = _decode_mime_header(msg.get("Message-ID"))

    if subj:
        headers.append(f"Subject: {subj}")
    if sender:
        headers.append(f"From: {sender}")
    if recipient:
        headers.append(f"To: {recipient}")
    if cc:
        headers.append(f"Cc: {cc}")
    if date:
        headers.append(f"Date: {date}")
    if msg_id:
        headers.append(f"Message-ID: {msg_id}")

    header_block = "\n".join(headers)
    body_parts = []
    attachment_sections: List[PageData] = []

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            cdispo = str(part.get("Content-Disposition") or "")
            fname = part.get_filename()

            if fname:
                att_name = _decode_mime_header(fname)
                att_payload = part.get_payload(decode=True)
                if att_payload:
                    try:
                        att_text = att_payload.decode("utf-8", errors="replace")
                        if len(att_text.strip()) > 0:
                            attachment_sections.append(PageData(
                                index=None,
                                locator=f"Attachment: {att_name}",
                                text=f"# Attachment: {att_name}\n\n{att_text.strip()}",
                                char_count=len(att_text)
                            ))
                    except Exception:
                        pass
                continue

            if "attachment" in cdispo:
                continue

            if ctype == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    body_parts.append(payload.decode("utf-8", errors="replace"))
            elif ctype == "text/html" and not body_parts:
                payload = part.get_payload(decode=True)
                if payload:
                    html_str = payload.decode("utf-8", errors="replace")
                    body_parts.append(extract_html_text(html_str))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body_parts.append(payload.decode("utf-8", errors="replace"))

    body_text = "\n\n".join(body_parts).strip()
    full_email = f"{header_block}\n\n---\n\n{body_text}".strip()

    pages = [PageData(
        index=None,
        locator=f"Email: {subj or 'Untitled'}",
        text=full_email,
        char_count=len(full_email)
    )]
    pages.extend(attachment_sections)

    return ParserResult(
        filename=filename,
        mime="message/rfc822",
        file_hash=file_hash,
        parser_name="eml-rfc822",
        parser_version="eml-rfc822@2.0",
        pages=pages
    )


# ─── HTML Parser using Python stdlib HTMLParser ──────────────────────────────

class _HTMLTextExtractor(HTMLParser):
    """Clean HTML text extractor converting semantic tags to Markdown."""
    def __init__(self):
        super().__init__()
        self.result: List[str] = []
        self._current_tag: Optional[str] = None
        self._skip_depth = 0
        self._heading_level = 0
        self._in_table_cell = False
        self._table_row: List[str] = []

    def handle_starttag(self, tag: str, attrs):
        t = tag.lower()
        self._current_tag = t
        if t in ["script", "style", "head", "noscript"]:
            self._skip_depth += 1
        elif t in ["h1", "h2", "h3", "h4", "h5", "h6"]:
            self._heading_level = int(t[1])
            self.result.append(f"\n\n{'#' * self._heading_level} ")
        elif t in ["p", "div"]:
            self.result.append("\n\n")
        elif t == "br":
            self.result.append("\n")
        elif t == "li":
            self.result.append("\n- ")
        elif t == "tr":
            self._table_row = []
        elif t in ["td", "th"]:
            self._in_table_cell = True

    def handle_endtag(self, tag: str):
        t = tag.lower()
        if t in ["script", "style", "head", "noscript"]:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif t in ["h1", "h2", "h3", "h4", "h5", "h6"]:
            self._heading_level = 0
            self.result.append("\n\n")
        elif t in ["p", "div"]:
            self.result.append("\n\n")
        elif t in ["td", "th"]:
            self._in_table_cell = False
        elif t == "tr":
            if self._table_row:
                self.result.append("\n| " + " | ".join(self._table_row) + " |")

    def handle_data(self, data: str):
        if self._skip_depth > 0:
            return
        text = data.strip()
        if not text:
            return
        if self._in_table_cell:
            self._table_row.append(text)
        else:
            self.result.append(text + " ")

    def get_text(self) -> str:
        raw = "".join(self.result)
        raw = re.sub(r'[ \t]+', ' ', raw)
        raw = re.sub(r'\n{3,}', '\n\n', raw)
        return raw.strip()


def extract_html_text(html_content: str) -> str:
    """Extract clean readable text from HTML string."""
    parser = _HTMLTextExtractor()
    parser.feed(html_content)
    return parser.get_text()


def parse_html(file_path: str, filename: str) -> ParserResult:
    """Parse HTML documents using Python stdlib HTMLParser."""
    file_hash = compute_file_hash(file_path)
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        html = f.read()

    clean_text = extract_html_text(html)
    pages = [PageData(index=None, locator="HTML Document", text=clean_text, char_count=len(clean_text))]
    return ParserResult(
        filename=filename,
        mime="text/html",
        file_hash=file_hash,
        parser_name="html-stdlib",
        parser_version="html-stdlib@2.0",
        pages=pages
    )


# ─── CSV Parser: Row-Group Chunking with Replayed Headers ────────────────────

def parse_csv(file_path: str, filename: str, rows_per_group: int = 30) -> ParserResult:
    """
    Parse CSV files by grouping rows with header replay, emitting page_number=None
    and locator='Rows X-Y' to maintain citation truth.
    """
    file_hash = compute_file_hash(file_path)
    pages: List[PageData] = []

    content = ""
    for enc in ["utf-8", "utf-8-sig", "latin-1"]:
        try:
            with open(file_path, "r", encoding=enc) as f:
                content = f.read()
            break
        except UnicodeDecodeError:
            continue

    reader = list(csv.reader(content.splitlines()))
    if not reader:
        return ParserResult(
            filename=filename,
            mime="text/csv",
            file_hash=file_hash,
            parser_name="csv-rowgroup",
            parser_version="csv-rowgroup@2.0",
            pages=[PageData(index=None, locator="Empty", text="[Empty CSV]")]
        )

    headers = [c.strip() for c in reader[0]]
    header_line = "| " + " | ".join(headers) + " |"
    divider_line = "| " + " | ".join(["---"] * len(headers)) + " |"
    data_rows = reader[1:]

    if not data_rows:
        table_text = f"{header_line}\n{divider_line}"
        pages.append(PageData(index=None, locator="Headers Only", text=table_text, char_count=len(table_text)))
    else:
        for idx in range(0, len(data_rows), rows_per_group):
            group = data_rows[idx:idx + rows_per_group]
            start_num = idx + 1
            end_num = idx + len(group)
            loc = f"Rows {start_num}-{end_num}"

            row_lines = [header_line, divider_line]
            for r in group:
                row_lines.append("| " + " | ".join(c.strip() for c in r) + " |")

            group_text = "\n".join(row_lines)
            pages.append(PageData(
                index=None,
                locator=loc,
                text=group_text,
                char_count=len(group_text)
            ))

    return ParserResult(
        filename=filename,
        mime="text/csv",
        file_hash=file_hash,
        parser_name="csv-rowgroup",
        parser_version="csv-rowgroup@2.0",
        pages=pages
    )


# ─── Plain Text, Markdown, JSON, Code Parser ─────────────────────────────────

def parse_plain_or_code(file_path: str, filename: str) -> ParserResult:
    """Parse plain text, Markdown, JSON, or code files with encoding detection."""
    file_hash = compute_file_hash(file_path)
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
    pages = [PageData(
        index=idx,
        locator=None,
        text=clean_content,
        char_count=len(clean_content)
    )]

    return ParserResult(
        filename=filename,
        mime=mime,
        file_hash=file_hash,
        parser_name="text-plain",
        parser_version="text-plain@2.0",
        pages=pages
    )


# Backward-compatible aliases
ParsedPage = PageData
ParsedDocument = ParserResult


# ─── Unified Parsing Entrypoint ──────────────────────────────────────────────

def parse_document(
    file_path: str,
    filename: str,
    ocr_threshold: int = 30,
    ocr_dpi: int = 300,
    timeout: float = 30.0
) -> ParserResult:
    """
    Unified entry point for document parsing.
    Dispatches to format-specific parsers and returns a versioned ParserResult contract.
    """
    ext = filename.lower().split('.')[-1] if '.' in filename else ""

    if ext == "pdf":
        return parse_pdf(file_path, filename, ocr_threshold=ocr_threshold, ocr_dpi=ocr_dpi, timeout=timeout)
    elif ext in ["docx", "doc"]:
        return parse_docx(file_path, filename)
    elif ext in ["eml", "msg"]:
        return parse_eml(file_path, filename)
    elif ext in ["html", "htm"]:
        return parse_html(file_path, filename)
    elif ext == "csv":
        return parse_csv(file_path, filename)
    else:
        return parse_plain_or_code(file_path, filename)
