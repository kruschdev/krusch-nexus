import os
import re
import csv
import json
import email
import zipfile
import logging
import hashlib
import tempfile
import subprocess
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("krusch_nexus.parsers")

# Resilient Document class compatibility (works both with and without llama_index)
try:
    from llama_index.core import Document
except ImportError:
    class Document:
        """Standalone Document class matching LlamaIndex interface for closed-loop environments."""
        def __init__(self, text: str = "", metadata: Optional[Dict[str, Any]] = None, doc_id: Optional[str] = None):
            self.text = text or ""
            self.metadata = metadata or {}
            self.doc_id = doc_id or ""

        def __repr__(self) -> str:
            snip = self.text[:40].replace('\n', ' ')
            return f"Document(text={snip!r}..., metadata={self.metadata})"


class ParsedPage:
    """Represents a single page or distinct structural section of a document."""
    def __init__(self, page_number: int, text: str, has_images: bool = False, ocr_applied: bool = False):
        self.page_number = page_number
        self.text = text.strip()
        self.has_images = has_images
        self.ocr_applied = ocr_applied

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page_number": self.page_number,
            "text": self.text,
            "has_images": self.has_images,
            "ocr_applied": self.ocr_applied
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


def _try_tesseract_ocr(pdf_path: str, page_num: int) -> Optional[str]:
    """Attempt local OCR fallback on a specific PDF page using pdftoppm + tesseract."""
    # Check if tesseract binary is available
    tess_path = subprocess.run(["which", "tesseract"], capture_output=True, text=True).stdout.strip()
    if not tess_path:
        return None

    with tempfile.TemporaryDirectory() as tmpdir:
        img_prefix = os.path.join(tmpdir, f"page_{page_num}")
        # Render PDF page to PNG at 150 DPI
        ppm_cmd = ["pdftoppm", "-png", "-r", "150", "-f", str(page_num), "-l", str(page_num), pdf_path, img_prefix]
        res = subprocess.run(ppm_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            return None

        # Find rendered image
        files = [f for f in os.listdir(tmpdir) if f.startswith(f"page_{page_num}") and f.endswith(".png")]
        if not files:
            return None

        img_file = os.path.join(tmpdir, files[0])
        ocr_cmd = ["tesseract", img_file, "stdout", "--oem", "1", "-l", "eng"]
        ocr_res = subprocess.run(ocr_cmd, capture_output=True, text=True)
        if ocr_res.returncode == 0 and ocr_res.stdout.strip():
            return ocr_res.stdout.strip()
    return None


def parse_pdf(file_path: str, filename: str) -> ParsedDocument:
    """
    Parse PDF page-by-page using pdftotext (Poppler) with local OCR fallback for scans.
    Preserves exact 1-based page numbers.
    """
    file_hash = compute_file_hash(file_path)
    pages: List[ParsedPage] = []

    # Run pdftotext with layout preservation
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", file_path, "-"],
            capture_output=True,
            text=True,
            errors="replace"
        )
        if proc.returncode == 0:
            # Form-feed character \x0c separates pages in pdftotext output
            raw_pages = proc.stdout.split("\x0c")
            # The last element after final form-feed is usually empty
            if raw_pages and not raw_pages[-1].strip():
                raw_pages.pop()

            for idx, page_raw in enumerate(raw_pages, 1):
                clean_text = page_raw.strip()
                ocr_applied = False
                has_images = False

                # If text is suspiciously short (< 30 chars), check for OCR fallback
                if len(clean_text) < 30:
                    has_images = True
                    ocr_text = _try_tesseract_ocr(file_path, idx)
                    if ocr_text and len(ocr_text) > len(clean_text):
                        clean_text = ocr_text
                        ocr_applied = True
                    elif not clean_text:
                        clean_text = f"[Scanned page {idx} - image only / OCR pending]"

                pages.append(ParsedPage(
                    page_number=idx,
                    text=clean_text,
                    has_images=has_images,
                    ocr_applied=ocr_applied
                ))
    except Exception as e:
        logger.warning(f"pdftotext failed for {filename} ({e}), falling back to direct byte inspection.")

    # Fallback if pdftotext returned nothing or failed
    if not pages:
        pages = [ParsedPage(page_number=1, text="[PDF document content could not be extracted]")]

    return ParsedDocument(filename=filename, file_hash=file_hash, pages=pages)


def parse_docx(file_path: str, filename: str) -> ParsedDocument:
    """
    Parse DOCX files by reading word/document.xml directly from the ZIP archive.
    Extracts headings, paragraphs, and table text without heavy dependencies.
    """
    file_hash = compute_file_hash(file_path)
    paragraphs = []

    try:
        with zipfile.ZipFile(file_path, "r") as docx_zip:
            if "word/document.xml" in docx_zip.namelist():
                xml_content = docx_zip.read("word/document.xml")
                root = ET.fromstring(xml_content)

                # XML namespaces
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

                for p in root.iter(f"{{{ns['w']}}}p"):
                    # Check for heading style
                    p_style = p.find(f".//{{{ns['w']}}}pStyle", ns)
                    is_heading = False
                    heading_level = 1
                    if p_style is not None:
                        val = p_style.attrib.get(f"{{{ns['w']}}}val", "")
                        if "heading" in val.lower():
                            is_heading = True
                            match = re.search(r'\d+', val)
                            if match:
                                heading_level = int(match.group(0))

                    # Extract all text runs
                    texts = [t.text for t in p.iter(f"{{{ns['w']}}}t") if t.text]
                    p_text = "".join(texts).strip()
                    if p_text:
                        if is_heading:
                            p_text = f"{'#' * min(heading_level, 4)} {p_text}"
                        paragraphs.append(p_text)

                # Also inspect tables
                for tbl in root.iter(f"{{{ns['w']}}}tbl"):
                    table_rows = []
                    for row in tbl.iter(f"{{{ns['w']}}}tr"):
                        cells = []
                        for cell in row.iter(f"{{{ns['w']}}}tc"):
                            c_texts = [t.text for t in cell.iter(f"{{{ns['w']}}}t") if t.text]
                            cells.append("".join(c_texts).strip())
                        if any(cells):
                            table_rows.append(" | ".join(cells))
                    if table_rows:
                        paragraphs.append("\n".join(table_rows))
    except Exception as e:
        logger.error(f"Error parsing DOCX {filename}: {e}")
        paragraphs = [f"[Error parsing DOCX: {e}]"]

    full_body = "\n\n".join(paragraphs) if paragraphs else "[Empty DOCX document]"
    pages = [ParsedPage(page_number=1, text=full_body)]
    return ParsedDocument(filename=filename, file_hash=file_hash, pages=pages)


def parse_eml(file_path: str, filename: str) -> ParsedDocument:
    """
    Parse RFC822 EML email files, extracting sender, recipients, subject, date,
    and plain text / cleaned HTML body.
    """
    file_hash = compute_file_hash(file_path)
    with open(file_path, "rb") as f:
        msg = email.message_from_binary_file(f)

    headers = []
    if msg.get("Subject"):
        headers.append(f"Subject: {msg['Subject']}")
    if msg.get("From"):
        headers.append(f"From: {msg['From']}")
    if msg.get("To"):
        headers.append(f"To: {msg['To']}")
    if msg.get("Cc"):
        headers.append(f"Cc: {msg['Cc']}")
    if msg.get("Date"):
        headers.append(f"Date: {msg['Date']}")

    header_block = "\n".join(headers)

    # Extract body parts
    body_parts = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            cdispo = str(part.get("Content-Disposition") or "")
            if "attachment" in cdispo:
                continue
            if ctype == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    body_parts.append(payload.decode("utf-8", errors="replace"))
            elif ctype == "text/html" and not body_parts:
                payload = part.get_payload(decode=True)
                if payload:
                    clean = re.sub(r'<[^>]+>', ' ', payload.decode("utf-8", errors="replace"))
                    clean = re.sub(r'\s+', ' ', clean).strip()
                    body_parts.append(clean)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body_parts.append(payload.decode("utf-8", errors="replace"))

    body_text = "\n\n".join(body_parts).strip()
    full_email = f"{header_block}\n\n---\n\n{body_text}".strip()

    pages = [ParsedPage(page_number=1, text=full_email)]
    metadata = {
        "subject": msg.get("Subject", ""),
        "from": msg.get("From", ""),
        "to": msg.get("To", ""),
        "date": msg.get("Date", ""),
        "doc_type": "email"
    }
    return ParsedDocument(filename=filename, file_hash=file_hash, pages=pages, metadata=metadata)


def parse_html(file_path: str, filename: str) -> ParsedDocument:
    """Parse HTML documents, extracting readable text and preserving headings."""
    file_hash = compute_file_hash(file_path)
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        html = f.read()

    # Strip script and style tags
    clean = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.DOTALL | re.IGNORECASE)

    # Convert headings to markdown
    for level in range(1, 7):
        clean = re.sub(rf'<h{level}[^>]*>(.*?)</h{level}>', rf'\n\n{"#" * level} \1\n\n', clean, flags=re.DOTALL | re.IGNORECASE)

    # Replace paragraph and line breaks
    clean = re.sub(r'<p[^>]*>', '\n\n', clean, flags=re.IGNORECASE)
    clean = re.sub(r'</p>', '\n', clean, flags=re.IGNORECASE)
    clean = re.sub(r'<br\s*/?>', '\n', clean, flags=re.IGNORECASE)
    clean = re.sub(r'<li[^>]*>', '\n- ', clean, flags=re.IGNORECASE)

    # Strip remaining HTML tags
    clean = re.sub(r'<[^>]+>', ' ', clean)
    # Unescape common entities
    clean = clean.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')
    clean = re.sub(r'\n{3,}', '\n\n', clean).strip()

    pages = [ParsedPage(page_number=1, text=clean)]
    return ParsedDocument(filename=filename, file_hash=file_hash, pages=pages)


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
            # Format CSV as markdown table for improved semantic retrieval
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


def parse_document(file_path: str, filename: str) -> List[Document]:
    """
    Unified entry point for document parsing.
    Dispatches to format-specific parsers based on extension.
    Returns a list of Document objects preserving page numbers and file hash.
    """
    ext = filename.lower().split('.')[-1] if '.' in filename else ""

    if ext == "pdf":
        parsed = parse_pdf(file_path, filename)
    elif ext in ["docx", "doc"]:
        parsed = parse_docx(file_path, filename)
    elif ext in ["eml", "msg"]:
        parsed = parse_eml(file_path, filename)
    elif ext in ["html", "htm"]:
        parsed = parse_html(file_path, filename)
    elif ext in ["txt", "md", "csv", "json", "py", "js", "ts", "yaml", "yml", "sql"]:
        parsed = parse_plain_or_code(file_path, filename)
    else:
        # Fallback to plain text read
        parsed = parse_plain_or_code(file_path, filename)

    return parsed.to_llama_documents()
