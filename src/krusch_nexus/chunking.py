"""
KruschNexus Citation-First Chunking Engine (chunking.py)
========================================================
Splits multi-page documents into structure-aware chunks while:
- Isolating raw text for embedding (no breadcrumbs prepended into embedding strings)
- Computing source_hash over raw text ONLY (breadcrumbs changes don't alter hashes)
- Carrying hierarchical heading stacks (e.g. Article IV > Section 8.22)
- Flowing sliding-window overlap across page boundaries
- Supporting DocType enums, StructuredLocator, and locator citations for unpaged formats
"""

import re
import hashlib
import logging
from typing import List, Dict, Any, Optional, Set, Tuple

from .models import PageData, DocType, StructuredLocator, format_citation

logger = logging.getLogger("krusch_nexus.chunking")

# Section heading patterns for legal and corporate statutory documents
SECTION_PATTERN = re.compile(
    r'(?:'
    r'(?:(?:[A-Za-z\.]+\s+)*(?:Code|U\.S\.C\.|Stat\.|C\.F\.R\.)\s*)?(?:§+|Section|Sec\.|Article|Art\.|Clause|Exhibit)\s*[0-9A-Za-z\.\-:]+(?:\([0-9A-Za-z]+\))*'
    r'|\b[0-9]{1,3}\.[0-9]{2,3}\.[0-9]{2,4}(?:\([A-Za-z0-9]+\))*'
    r')(?:\s+[A-Za-z0-9\s,\-\'\":]{0,60})?',
    re.IGNORECASE
)
OPERATIVE_PATTERN = re.compile(
    r'(?:§+|Section|Sec\.|Article|Art\.|Clause)\s*([0-9IVXLCDM]+)',
    re.IGNORECASE
)
MARKDOWN_HEADING_PATTERN = re.compile(r'^(#{1,6}\s+[^\n]+)', re.MULTILINE)


def normalize_statute_citation(query: str) -> Dict[str, Any]:
    """
    Formalized citation query normalization function.
    Parses statutory section references (e.g. 'Cal. Civ. Code § 1950.5', 'Section 8.22.030(C)', 'Art. IV').
    Returns a structured dictionary with normalized token, canonical representation, and match status.
    """
    m = SECTION_PATTERN.search(query)
    standalone = None
    if not m:
        standalone = re.search(r'\b([0-9]{1,4}(?:\.[0-9]+)*(?:\([0-9A-Za-z]+\))+|\b[0-9]{1,4}\.[0-9]+)\b', query)
        if not standalone:
            return {
                "matched": False,
                "raw_query": query,
                "canonical_token": "",
                "normalized_token": "",
                "section_number": ""
            }

    target = m.group(0).strip() if m else standalone.group(0).strip()
    num_match = re.search(
        r'(?:§+|Section|Sec\.|Article|Art\.|Clause|Exhibit)\s*([0-9A-Za-z\.\-:]+(?:\([0-9A-Za-z]+\))*)'
        r'|\b([0-9]+(?:\.[0-9]+)*(?:\([0-9A-Za-z]+\))+)'
        r'|\b([0-9]+(?:\.[0-9]+)+)',
        target,
        re.IGNORECASE
    )
    if num_match:
        raw_num = num_match.group(1) or num_match.group(2) or num_match.group(3)
        clean_sec = re.sub(r'\(.*?\)', '', raw_num).strip()
    else:
        clean_sec = target

    canonical = f"§ {clean_sec}" if not target.lower().startswith("art") else target
    clean_norm = re.sub(r'[^a-z0-9]', '', clean_sec.lower())

    return {
        "matched": True,
        "raw_query": query,
        "canonical_token": canonical,
        "normalized_token": clean_norm,
        "section_number": clean_sec
    }


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
        structured_locator: Optional[StructuredLocator] = None,
        metadata: Optional[Dict[str, Any]] = None,
        char_start: Optional[int] = None,
        char_end: Optional[int] = None,
        confidence: Optional[float] = None,
        bbox: Optional[List[float]] = None,
        chunker_version: str = "1.0",
        embed_model: str = "bge-large"
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
        self.bbox = bbox
        self.structured_locator = structured_locator or StructuredLocator.from_raw(
            page=page_number, locator_str=locator, header=header, bbox=bbox
        )
        self.heading_path = list(self.structured_locator.path) if (self.structured_locator and self.structured_locator.path) else []
        self.metadata = metadata or {}
        self.char_start = char_start
        self.char_end = char_end
        self.confidence = confidence
        self.chunker_version = chunker_version
        self.embed_model = embed_model

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "raw_text": self.raw_text,
            "citation": self.citation,
            "header": self.header,
            "locator": self.locator,
            "structured_locator": self.structured_locator.model_dump() if self.structured_locator else None,
            "heading_path": self.heading_path,
            "page_number": self.page_number,
            "chunk_index": self.chunk_index,
            "source_hash": self.source_hash,
            "doc_hash": self.doc_hash,
            "filename": self.filename,
            "metadata": self.metadata,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "bbox": self.bbox,
            "confidence": self.confidence,
            "chunker_version": self.chunker_version,
            "embed_model": self.embed_model
        }


def compute_chunk_hash(text: str) -> str:
    """Compute SHA-256 hash of normalized raw text only."""
    clean = re.sub(r'\s+', ' ', text).strip()
    return hashlib.sha256(clean.encode('utf-8')).hexdigest()


SUBSECTION_PATTERN = re.compile(r'^\s*(\([a-z0-9]+\)|\b[0-9]+[a-z]?\.\b|[A-Z]\.)\s+', re.IGNORECASE)


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
    return format_citation(filename=filename, page_number=page_number, locator=locator, header=header)


def union_bboxes(bboxes: List[List[float]]) -> Optional[List[float]]:
    """Compute the bounding box union [left, top, width, height] for multiple boxes."""
    valid = [b for b in bboxes if b and len(b) == 4]
    if not valid:
        return None
    min_x = min(b[0] for b in valid)
    min_y = min(b[1] for b in valid)
    max_r = max(b[0] + b[2] for b in valid)
    max_b = max(b[1] + b[3] for b in valid)
    return [round(min_x, 2), round(min_y, 2), round(max_r - min_x, 2), round(max_b - min_y, 2)]


def chunk_document_pages(
    pages: List[PageData],
    filename: str,
    file_hash: str,
    max_chars: int = 1800,
    overlap_chars: int = 150,
    doc_type: DocType = DocType.GENERAL,
    base_metadata: Optional[Dict[str, Any]] = None,
    chunker_version: str = "1.0",
    embed_model: str = "bge-large"
) -> List[Chunk]:
    """
    Split document pages into structure-aware chunks.
    - Embed text is purely raw section text.
    - source_hash is calculated strictly on raw_text.
    - Heading stacks are tracked across the document.
    - Overlap flows structurally across boundaries.
    - Tracks char_start / char_end spatial offsets and PDF bounding boxes.
    """
    chunks: List[Chunk] = []
    global_chunk_idx = 0
    heading_stack: List[str] = []

    base_meta = dict(base_metadata or {})
    resolved_doc_type = doc_type.value if isinstance(doc_type, DocType) else str(doc_type)
    base_meta["doc_type"] = resolved_doc_type

    # Flatten all elements with provenance: (page_num, locator, text, confidence, char_start, char_end, is_header, bbox)
    elements: List[Tuple[Optional[int], Optional[str], str, Optional[float], Optional[int], Optional[int], bool, Optional[List[float]]]] = []
    for p in pages:
        p_text = p.text
        if not p_text.strip():
            continue
        page_idx = p.index if p.index is not None else getattr(p, "page_number", None)
        p_conf = getattr(p, "confidence", None)
        block_map = {b.text.strip(): b.bbox for b in p.blocks if b.bbox and b.text.strip()}

        curr_lines: List[str] = []
        c_start: Optional[int] = None
        c_end: Optional[int] = None
        curr_bboxes: List[List[float]] = []

        for match in re.finditer(r'[^\r\n]+', p_text):
            line = match.group(0).strip()
            if not line:
                continue
            line_bbox = block_map.get(line)
            hdr = detect_header_candidate(line)
            if hdr:
                if curr_lines:
                    comb_bbox = union_bboxes(curr_bboxes) if curr_bboxes else None
                    elements.append((page_idx, p.locator, '\n'.join(curr_lines), p_conf, c_start, c_end, False, comb_bbox))
                    curr_lines = []
                    curr_bboxes = []
                    c_start = None
                elements.append((page_idx, p.locator, line, p_conf, match.start(), match.end(), True, line_bbox))
            else:
                if not curr_lines:
                    c_start = match.start()
                curr_lines.append(line)
                if line_bbox:
                    curr_bboxes.append(line_bbox)
                c_end = match.end()

        if curr_lines:
            comb_bbox = union_bboxes(curr_bboxes) if curr_bboxes else None
            elements.append((page_idx, p.locator, '\n'.join(curr_lines), p_conf, c_start, c_end, False, comb_bbox))

    if not elements:
        return []

    current_items: List[Tuple[Optional[int], Optional[str], str, Optional[float], Optional[int], Optional[int], bool, Optional[List[float]]]] = []
    current_len = 0
    current_header = "General"
    overlap_item_count = 0
    has_body_in_chunk = False
    has_operative_in_chunk = False

    def flush_current_chunk(clear_overlap: bool = False):
        nonlocal global_chunk_idx, current_items, current_len, overlap_item_count, has_body_in_chunk, has_operative_in_chunk
        if not current_items:
            return

        raw_chunk = "\n\n".join(item[2] for item in current_items).strip()

        # Primary page and locator
        primary_item = current_items[overlap_item_count] if len(current_items) > overlap_item_count else current_items[0]
        first_page = primary_item[0] if primary_item[0] is not None else current_items[0][0]
        first_loc = primary_item[1] if primary_item[1] is not None else current_items[0][1]

        # Use breadcrumb stack
        loc = " > ".join(heading_stack) if (first_page is None and heading_stack) else first_loc
        c_hash = compute_chunk_hash(raw_chunk)
        cit_str = format_chunk_citation(filename, page_number=first_page, locator=loc, header=current_header)

        c_start = current_items[0][4]
        c_end = current_items[-1][5]
        item_confs = [x[3] for x in current_items if x[3] is not None]
        chunk_conf = sum(item_confs) / len(item_confs) if item_confs else None

        # Bounding box union
        item_bboxes = [x[7] for x in current_items if len(x) > 7 and x[7] is not None]
        chunk_bbox = union_bboxes(item_bboxes)

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
            structured_locator=StructuredLocator.from_raw(page=first_page, locator_str=loc, header=current_header, bbox=chunk_bbox),
            metadata=chunk_meta,
            char_start=c_start,
            char_end=c_end,
            confidence=chunk_conf,
            bbox=chunk_bbox,
            chunker_version=chunker_version,
            embed_model=embed_model
        ))
        global_chunk_idx += 1

        if clear_overlap:
            current_items = []
            current_len = 0
            overlap_item_count = 0
            has_body_in_chunk = False
            has_operative_in_chunk = False
            return

        overlap_items = []
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
        has_body_in_chunk = any(not x[6] for x in current_items)
        has_operative_in_chunk = any(x[6] and bool(OPERATIVE_PATTERN.search(x[2]) or x[2].startswith('#')) for x in current_items)

    for item in elements:
        page_num, loc, para, conf, c_start, c_end, is_hdr, *rest = item
        first_line = para.split('\n')[0]
        detected = detect_header_candidate(first_line)

        if detected:
            is_operative = bool(OPERATIVE_PATTERN.search(first_line) or first_line.startswith('#'))
            should_flush = False
            if current_items:
                if has_operative_in_chunk or has_body_in_chunk:
                    should_flush = True
                elif current_items[0][0] != page_num:
                    should_flush = True
            if should_flush:
                flush_current_chunk(clear_overlap=True)
            title = detected
            if title not in heading_stack:
                heading_stack.append(title)
            current_header = title
            if is_operative:
                has_operative_in_chunk = True
        else:
            has_body_in_chunk = True

        para_len = len(para)

        if para_len > max_chars:
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', para) if s.strip()]
            s_offset = c_start or 0
            for s in sentences:
                s_len = len(s)
                if current_len + s_len + 2 > max_chars and current_items:
                    flush_current_chunk()
                current_items.append((page_num, loc, s, conf, s_offset, s_offset + s_len, False))
                current_len += s_len + 2
                s_offset += s_len + 1
        elif current_len + para_len + 2 > max_chars and current_items:
            flush_current_chunk()
            current_items.append(item)
            current_len += para_len + 2
        else:
            current_items.append(item)
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
