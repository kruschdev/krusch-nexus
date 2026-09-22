"""
KruschNexus Hardened Document Parsers
====================================
Zero-heavy-dependency document extraction pipeline with:
- True document-order table extraction for DOCX
- Image XObject detection & bounded OCR fallback for PDF
- Python stdlib HTMLParser (no brittle regex)
- RFC2047 MIME encoded-word header decoding for EML
- Cross-platform binary resolution via shutil.which
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

logger = logging.getLogger("krusch_nexus.parsers")


class Document:
    """Standalone Document class matching standard LlamaIndex interface for closed-loop environments."""
    def __init__(self, text: str = "", metadata: Optional[Dict[str, Any]] = None, doc_id: Optional[str] = None):
        self.text = text or ""
        self.metadata = metadata or {}
        self.doc_id = doc_id or ""

    def __repr__(self) -> str:
        snip = self.text[:40].replace('\n', ' ')
        return f"Document(text={snip!r}..., metadata={self.metadata})"


class ParsedPage:
    """Represents a single page or distinct structural section of a document."""
    def __init__(
        self,
        page_number: int,
        text: str,
        has_images: bool = False,
        ocr_applied: bool = False,
        ocr_confidence: Optional[float] = None
    ):
        self.page_number = page_number
        self.text = text.strip()
        self.has_images = has_images
        self.ocr_applied = ocr_applied
        self.ocr_confidence = ocr_confidence

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page_number": self.page_number,
            "text": self.text,
            "has_images": self.has_images,
            "ocr_applied": self.ocr_applied,
            "ocr_confidence": self.ocr_confidence
        }


class ParsedDocument:
    """Represents a fully parsed document with multi-page structure and provenance metadata."""
    def __init__(self, filename: str, file_hash: str, pages: List[ParsedPage], metadata: Optional[Dict[str, Any]] = None):
        self.filename = filename
        self.file_hash = file_hash
        self.pages = pages
        self.metadata = metadata or {}

    @property
    def total_pages(self) -> int:
        return len(self.pages)

    @property
    def total_chars(self) -> int:
        return sum(len(p.text) for p in self.pages)

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages if p.text)

    def to_llama_documents(self, base_metadata: Optional[Dict[str, Any]] = None) -> List[Document]:
        """Convert parsed pages into Document objects carrying page_number, filename, and file_hash."""
        docs = []
        base = dict(self.metadata)
        if base_metadata:
            base.update(base_metadata)

        for page in self.pages:
            meta = {
                **base,
                "filename": self.filename,
                "file_hash": self.file_hash,
                "page_number": page.page_number,
                "page_label": str(page.page_number),
                "ocr_applied": page.ocr_applied,
                "ocr_confidence": page.ocr_confidence,
                "has_images": page.has_images
            }
            docs.append(Document(text=page.text, metadata=meta))
        return docs


def compute_file_hash(file_path: str) -> str:
    """Compute SHA-256 hash of a file for exact deduplication."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


# ─── PDF Parser with Image Detection & OCR Fallback ──────────────────────────

def _pdf_contains_images(file_path: str) -> bool:
    """Fast check whether PDF raw stream contains XObject image definitions."""
    try:
        with open(file_path, "rb") as f:
            content = f.read(1_000_000)  # Check first 1MB
            if b"/Image" in content or b"/XObject" in content:
                return True
    except Exception:
        pass
    return False


def _try_tesseract_ocr(
    pdf_path: str,
    page_num: int,
    dpi: int = 150,
    timeout: float = 30.0
) -> Optional[str]:
    """Attempt local OCR fallback on a specific PDF page using pdftoppm + tesseract."""
    tess_path = shutil.which("tesseract")
    ppm_path = shutil.which("pdftoppm")
    if not tess_path or not ppm_path:
        return None

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
        except (subprocess.TimeoutExpired, Exception) as e:
            logger.warning(f"pdftoppm timed out or failed for page {page_num}: {e}")
            return None

        files = [f for f in os.listdir(tmpdir) if f.startswith(f"page_{page_num}") and f.endswith(".png")]
        if not files:
            return None

        img_file = os.path.join(tmpdir, files[0])
        # Pin PSM 3 (fully automatic page segmentation) and language eng with strict timeout
        ocr_cmd = [tess_path, img_file, "stdout", "--oem", "1", "--psm", "3", "-l", "eng"]
        try:
            ocr_res = subprocess.run(ocr_cmd, capture_output=True, text=True, timeout=timeout)
            if ocr_res.returncode == 0:
                extracted = ocr_res.stdout.strip()
                # Verify extracted text contains meaningful characters, not garbage
                printable = "".join(c for c in extracted if c.isalnum() or c in " .,;:!?-\n")
                if len(printable) >= 5:
                    return extracted
        except (subprocess.TimeoutExpired, Exception) as e:
            logger.warning(f"Tesseract OCR timed out or failed for page {page_num}: {e}")

    return None


def parse_pdf(
    file_path: str,
    filename: str,
    ocr_threshold: int = 30,
    ocr_dpi: int = 150,
    timeout: float = 30.0
) -> ParsedDocument:
    """
    Parse PDF page-by-page using pdftotext (Poppler) with local OCR fallback for scans.
    Preserves exact 1-based page numbers.
    """
    file_hash = compute_file_hash(file_path)
    pages: List[ParsedPage] = []
    has_image_streams = _pdf_contains_images(file_path)

    pdftotext_bin = shutil.which("pdftotext")
    if pdftotext_bin:
        try:
            proc = subprocess.run(
                [pdftotext_bin, "-layout", file_path, "-"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout
            )
            if proc.returncode == 0:
                raw_pages = proc.stdout.split("\x0c")
                if raw_pages and not raw_pages[-1].strip():
                    raw_pages.pop()

                for idx, page_raw in enumerate(raw_pages, 1):
                    clean_text = page_raw.strip()
                    ocr_applied = False
                    has_images = False

                    # If text is suspiciously short (< ocr_threshold chars) and document has images, attempt OCR
                    if len(clean_text) < ocr_threshold:
                        if has_image_streams:
                            has_images = True
                            ocr_text = _try_tesseract_ocr(file_path, idx, dpi=ocr_dpi, timeout=timeout)
                            if ocr_text and len(ocr_text) > len(clean_text):
                                clean_text = ocr_text
                                ocr_applied = True
                                logger.info(f"OCR applied to page {idx} of '{filename}' ({len(ocr_text)} chars)")
                            elif not clean_text:
                                clean_text = f"[Scanned page {idx} - image text pending]"
                        else:
                            # Truly blank or whitespace cover letter - do NOT waste time calling OCR
                            if not clean_text:
                                clean_text = f"[Blank page {idx}]"

                    pages.append(ParsedPage(
                        page_number=idx,
                        text=clean_text,
                        has_images=has_images,
                        ocr_applied=ocr_applied
                    ))
        except (subprocess.TimeoutExpired, Exception) as e:
            logger.warning(f"pdftotext failed for {filename} ({e})")

    if not pages:
        pages = [ParsedPage(page_number=1, text="[PDF document content could not be extracted]")]

    return ParsedDocument(filename=filename, file_hash=file_hash, pages=pages)


# ─── DOCX Parser with In-Order Table Extraction ──────────────────────────────

def parse_docx(file_path: str, filename: str) -> ParsedDocument:
    """
    Parse DOCX files by reading word/document.xml directly from ZIP.
    Extracts paragraphs, headings, and tables in strict document order.
    """
    file_hash = compute_file_hash(file_path)
    document_elements = []

    try:
        with zipfile.ZipFile(file_path, "r") as docx_zip:
            if "word/document.xml" in docx_zip.namelist():
                xml_content = docx_zip.read("word/document.xml")
                root = ET.fromstring(xml_content)
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

                body = root.find(f"{{{ns['w']}}}body", ns)
                if body is not None:
                    # Iterate direct children of body in natural document sequence
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
                            if p_text:
                                if is_heading:
                                    p_text = f"{'#' * min(heading_level, 4)} {p_text}"
                                document_elements.append(p_text)

                        # Case 2: Table (<w:tbl>) in exact document order
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
                                # Insert markdown table header divider if more than 1 row
                                if len(table_rows) > 1:
                                    col_count = len(table_rows[0].split('|')) - 2
                                    divider = "| " + " | ".join(["---"] * max(1, col_count)) + " |"
                                    table_rows.insert(1, divider)
                                document_elements.append("\n".join(table_rows))
    except Exception as e:
        logger.error(f"Error parsing DOCX {filename}: {e}")
        document_elements = [f"[Error parsing DOCX: {e}]"]

    full_body = "\n\n".join(document_elements) if document_elements else "[Empty DOCX document]"
    pages = [ParsedPage(page_number=1, text=full_body)]
    return ParsedDocument(filename=filename, file_hash=file_hash, pages=pages)


# ─── EML Parser with MIME Header Decoding ────────────────────────────────────

def _decode_mime_header(header_value: Optional[str]) -> str:
    """Decode RFC2047 MIME encoded-word strings (e.g., =?utf-8?B?...?=)."""
    if not header_value:
        return ""
    try:
        decoded_parts = decode_header(header_value)
        return str(make_header(decoded_parts)).strip()
    except Exception:
        return str(header_value).strip()


def parse_eml(file_path: str, filename: str) -> ParsedDocument:
    """
    Parse RFC822 EML email files, decoding MIME encoded headers and extracting
    body and attachment provenance.
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

    header_block = "\n".join(headers)

    body_parts = []
    attachments: List[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            cdispo = str(part.get("Content-Disposition") or "")
            fname = part.get_filename()
            if fname:
                attachments.append(_decode_mime_header(fname))

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

    if attachments:
        full_email += f"\n\nAttachments: {', '.join(attachments)}"

    pages = [ParsedPage(page_number=1, text=full_email)]
    metadata = {
        "subject": subj,
        "from": sender,
        "to": recipient,
        "date": date,
        "attachments": attachments,
        "doc_type": "email"
    }
    return ParsedDocument(filename=filename, file_hash=file_hash, pages=pages, metadata=metadata)


# ─── HTML Parser using Python stdlib HTMLParser ──────────────────────────────

class _HTMLTextExtractor(HTMLParser):
    """Clean, robust HTML text extractor converting semantic tags to markdown."""
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
        # Normalize double newlines and spaces
        raw = re.sub(r'[ \t]+', ' ', raw)
        raw = re.sub(r'\n{3,}', '\n\n', raw)
        return raw.strip()


def extract_html_text(html_content: str) -> str:
    """Extract clean readable text from HTML string."""
    parser = _HTMLTextExtractor()
    parser.feed(html_content)
    return parser.get_text()


def parse_html(file_path: str, filename: str) -> ParsedDocument:
    """Parse HTML documents using Python stdlib HTMLParser."""
    file_hash = compute_file_hash(file_path)
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        html = f.read()

    clean_text = extract_html_text(html)
    pages = [ParsedPage(page_number=1, text=clean_text)]
    return ParsedDocument(filename=filename, file_hash=file_hash, pages=pages)


# ─── Plain Text, CSV, JSON, Code Parser ──────────────────────────────────────

def parse_plain_or_code(file_path: str, filename: str) -> ParsedDocument:
    """Parse plain text, Markdown, CSV, or JSON files with encoding detection."""
    file_hash = compute_file_hash(file_path)
    content = ""

    for enc in ["utf-8", "utf-8-sig", "latin-1"]:
        try:
            with open(file_path, "r", encoding=enc) as f:
                content = f.read()
            break
        except UnicodeDecodeError:
            continue

    ext = filename.lower().split('.')[-1]
    if ext == "csv":
        try:
            lines = content.splitlines()
            reader = csv.reader(lines)
            table_lines = []
            for i, row in enumerate(reader):
                if not any(row):
                    continue
                table_lines.append("| " + " | ".join(c.strip() for c in row) + " |")
                if i == 0:
                    table_lines.append("| " + " | ".join(["---"] * len(row)) + " |")
            if table_lines:
                content = "\n".join(table_lines)
        except Exception:
            pass
    elif ext == "json":
        try:
            parsed = json.loads(content)
            content = json.dumps(parsed, indent=2)
        except Exception:
            pass

    pages = [ParsedPage(page_number=1, text=content.strip())]
    return ParsedDocument(filename=filename, file_hash=file_hash, pages=pages)


# ─── Unified Parsing Entrypoint ──────────────────────────────────────────────

def parse_document(
    file_path: str,
    filename: str,
    ocr_threshold: int = 30,
    ocr_dpi: int = 150,
    timeout: float = 30.0
) -> List[Document]:
    """
    Unified entry point for document parsing.
    Dispatches to format-specific parsers and returns Document objects with page fidelity.
    """
    ext = filename.lower().split('.')[-1] if '.' in filename else ""

    if ext == "pdf":
        parsed = parse_pdf(file_path, filename, ocr_threshold=ocr_threshold, ocr_dpi=ocr_dpi, timeout=timeout)
    elif ext in ["docx", "doc"]:
        parsed = parse_docx(file_path, filename)
    elif ext in ["eml", "msg"]:
        parsed = parse_eml(file_path, filename)
    elif ext in ["html", "htm"]:
        parsed = parse_html(file_path, filename)
    elif ext in ["txt", "md", "csv", "json", "py", "js", "ts", "yaml", "yml", "sql"]:
        parsed = parse_plain_or_code(file_path, filename)
    else:
        parsed = parse_plain_or_code(file_path, filename)

    return parsed.to_llama_documents()
