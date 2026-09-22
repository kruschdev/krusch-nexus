"""
KruschNexus Citation-First Chunking Engine (chunking.py)
========================================================
Splits multi-page documents into structure-aware chunks while:
- Isolating raw text for embedding (no breadcrumbs prepended into embedding strings)
- Computing source_hash over raw text ONLY (breadcrumbs changes don't alter hashes)
- Carrying hierarchical heading stacks (e.g. Article IV > Section 8.22)
- Flowing sliding-window overlap across page boundaries
- Supporting DocType enums and locator citations for unpaged formats (DOCX/CSV)
"""

import re
import hashlib
import logging
from typing import List, Dict, Any, Optional, Set, Tuple

from .models import PageData, DocType, Citation

logger = logging.getLogger("krusch_nexus.chunking")

# Section heading patterns for legal and corporate statutory documents
SECTION_PATTERN = re.compile(
    r'(?:§+|Section|Sec\.|Article|Clause)\s*([0-9]+[A-Za-z0-9\.\-:]*(?:\s+[A-Za-z0-9\s,\-\'\":]{0,60})?)',
    re.IGNORECASE
)
MARKDOWN_HEADING_PATTERN = re.compile(r'^(#{1,6}\s+[^\n]+)', re.MULTILINE)


class Chunk:
    """Represents a structurally aware document chunk with strict provenance."""
    def __init__(
        self,
        text: str,              # Raw text to embed (no breadcrumb prefix)
        raw_text: str,          # Unmodified chunk body
        citation: str,          # Canonical citation (e.g. 'doc.pdf p.3 § 1950.5' or 'memo.docx § Art. IV')
        header: Optional[str],
        locator: Optional[str],
        page_number: Optional[int],
        chunk_index: int,
        source_hash: str,       # SHA-256 over raw_text ONLY
        doc_hash: str,
        filename: str,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.text = text
        self.raw_text = raw_text
        self.citation = citation
        self.header = header
        self.locator = locator
        self.page_number = page_number
        self.chunk_index = chunk_index
        self.source_hash = source_hash
        self.doc_hash = doc_hash
        self.filename = filename
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "raw_text": self.raw_text,
            "citation": self.citation,
            "header": self.header,
            "locator": self.locator,
            "page_number": self.page_number,
            "chunk_index": self.chunk_index,
            "source_hash": self.source_hash,
            "doc_hash": self.doc_hash,
            "filename": self.filename,
            "metadata": self.metadata
        }


def compute_chunk_hash(text: str) -> str:
    """Compute SHA-256 hash of normalized raw text only."""
    clean = re.sub(r'\s+', ' ', text).strip()
    return hashlib.sha256(clean.encode('utf-8')).hexdigest()


def detect_header_candidate(line: str) -> Optional[str]:
    """
    Inspect whether a single line qualifies as a structural heading or section title.
    Returns clean title string or None.
    """
    clean = line.strip()
    if not clean or len(clean) > 120:
        return None

    # Check Markdown heading (# to ######)
    if clean.startswith('#'):
        return re.sub(r'^#+\s*', '', clean).strip()

    # Check Section / Article pattern
    sec_match = SECTION_PATTERN.search(clean)
    if sec_match:
        if len(clean) <= 90 and not clean.endswith("."):
            return clean
        return sec_match.group(0).strip()

    # Check All-Caps short title
    if clean.isupper() and 4 < len(clean) < 80 and not any(p in clean for p in [".", ";", "!", "?"]):
        return clean.title()

    return None


def format_chunk_citation(
    filename: str,
    page_number: Optional[int],
    locator: Optional[str] = None,
    header: Optional[str] = None
) -> str:
    """Format canonical citation string."""
    cit = Citation(filename=filename, page_number=page_number, locator=locator, header=header)
    return cit.formatted()


def chunk_document_pages(
    pages: List[PageData],
    filename: str,
    file_hash: str,
    max_chars: int = 1800,
    overlap_chars: int = 150,
    doc_type: DocType = DocType.GENERAL,
    base_metadata: Optional[Dict[str, Any]] = None
) -> List[Chunk]:
    """
    Split document pages into structure-aware chunks.
    - Embed text is purely raw section text.
    - source_hash is calculated strictly on raw_text.
    - Heading stacks are tracked across the document.
    - Overlap seamlessly crosses page boundaries.
    """
    chunks: List[Chunk] = []
    global_chunk_idx = 0
    heading_stack: List[str] = []

    base_meta = dict(base_metadata or {})
    resolved_doc_type = doc_type.value if isinstance(doc_type, DocType) else str(doc_type)
    base_meta["doc_type"] = resolved_doc_type

    # 1. Flatten all elements with provenance: (page_num, locator, text)
    elements: List[Tuple[Optional[int], Optional[str], str]] = []
    for p in pages:
        p_text = p.text.strip()
        if not p_text:
            continue
        paragraphs = [para.strip() for para in re.split(r'\n\s*\n', p_text) if para.strip()]
        if not paragraphs:
            paragraphs = [p_text]

        for para in paragraphs:
            page_idx = p.index if p.index is not None else getattr(p, "page_number", None)
            elements.append((page_idx, p.locator, para))

    if not elements:
        return []

    # 2. Sliding window chunking with cross-page overlap
    current_items: List[Tuple[Optional[int], Optional[str], str]] = []
    current_len = 0
    current_header = "General"

    overlap_item_count = 0

    def flush_current_chunk(clear_overlap: bool = False):
        nonlocal global_chunk_idx, current_items, current_len, overlap_item_count
        if not current_items:
            return

        raw_chunk = "\n\n".join(item[2] for item in current_items).strip()

        # Identify primary page (first non-overlap item if available)
        primary_item = current_items[overlap_item_count] if len(current_items) > overlap_item_count else current_items[0]
        first_page = primary_item[0] if primary_item[0] is not None else current_items[0][0]
        first_loc = primary_item[1] if primary_item[1] is not None else current_items[0][1]

        # Use current locator stack if unpaged
        loc = " > ".join(heading_stack) if (first_page is None and heading_stack) else first_loc
        c_hash = compute_chunk_hash(raw_chunk)
        cit_str = format_chunk_citation(filename, page_number=first_page, locator=loc, header=current_header)

        chunk_meta = {**base_meta, "doc_type": resolved_doc_type}
        chunks.append(Chunk(
            text=raw_chunk,          # Embed raw text only!
            raw_text=raw_chunk,
            citation=cit_str,
            header=current_header,
            locator=loc,
            page_number=first_page,
            chunk_index=global_chunk_idx,
            source_hash=c_hash,      # Hashed on raw text only!
            doc_hash=file_hash,
            filename=filename,
            metadata=chunk_meta
        ))
        global_chunk_idx += 1

        if clear_overlap:
            current_items = []
            current_len = 0
            overlap_item_count = 0
            return

        # Extract overlap items for next window
        overlap_items: List[Tuple[Optional[int], Optional[str], str]] = []
        accum = 0
        for item in reversed(current_items):
            item_len = len(item[2])
            if accum + item_len <= overlap_chars:
                overlap_items.insert(0, item)
                accum += item_len + 2
            else:
                break

        current_items = overlap_items
        overlap_item_count = len(overlap_items)
        current_len = sum(len(x[2]) for x in current_items) + 2 * max(0, len(current_items) - 1)

    for page_num, loc, para in elements:
        first_line = para.split('\n')[0]
        detected = detect_header_candidate(first_line)

        # Update heading stack
        if detected:
            if current_items:
                flush_current_chunk(clear_overlap=True)
            title = detected
            if title not in heading_stack:
                heading_stack.append(title)
            current_header = title


        para_len = len(para)

        # Handle massive single paragraphs
        if para_len > max_chars:
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', para) if s.strip()]
            for s in sentences:
                s_len = len(s)
                if current_len + s_len + 2 > max_chars and current_items:
                    flush_current_chunk()
                current_items.append((page_num, loc, s))
                current_len += s_len + 2
        elif current_len + para_len + 2 > max_chars and current_items:
            flush_current_chunk()
            current_items.append((page_num, loc, para))
            current_len += para_len + 2
        else:
            current_items.append((page_num, loc, para))
            current_len += para_len + 2

    if current_items:
        flush_current_chunk()

    return chunks


def deduplicate_chunks(chunks: List[Chunk], seen_hashes: Optional[Set[str]] = None) -> List[Chunk]:
    """Filter out duplicate chunks by exact source_hash."""
    seen = seen_hashes if seen_hashes is not None else set()
    unique = []
    for c in chunks:
        if c.source_hash not in seen:
            seen.add(c.source_hash)
            unique.append(c)
    return unique
