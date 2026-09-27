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

# Running noise, stamps, page numbers, and confidentiality banners that must NEVER become headers
NOISE_LINE_REGEX = re.compile(
    r'^(?:'
    r'page\s+\d+(?:\s+of\s+\d+)?'
    r'|\d+\s*[\/\-]\s*\d+'
    r'|-\s*\d+\s*-'
    r'|\d+'
    r'|confidential'
    r'|privileged'
    r'|attorney-client\s+privilege'
    r'|all\s+rights\s+reserved'
    r'|[A-Z]{2,12}[-_\s]*\d{4,12}'  # Bates stamp
    r'|(?:EXHIBIT|(?:PLTF|DEF|GOV|STATE)\s+EX(?:HIBIT)?)\s*(?:#|NO\.?)?\s*[\w\.\-]+'
    r'|(?:TRANSMISSION|FAX|SENT|RCVD)\b.*'
    r'|Case\s+[0-9]+:[0-9]{2}-[a-z]{2,4}-[0-9]+.*'
    r')$',
    re.IGNORECASE
)

# Table of Contents lines with dotted or dashed leaders ending in page numbers
TOC_LINE_REGEX = re.compile(r'(?:\.{3,}|_{3,}|\-{3,})\s*(?:\d+|[ivxlcdm]+)$', re.IGNORECASE)

# Anchored section pattern (must appear at start of line or following markdown hashes)
ANCHORED_SECTION_REGEX = re.compile(
    r'^(?:'
    r'(?:(?:[A-Za-z\.]+\s+)*(?:Code|U\.S\.C\.|Stat\.|C\.F\.R\.)\s*)?(?:§+|Section|Sec\.|Article|Art\.|Clause|Exhibit)\s*[0-9A-Za-z\.\-:]+(?:\([0-9A-Za-z]+\))*'
    r'|\b[0-9]{1,3}\.[0-9]{2,3}(?:\.[0-9]{2,4})?(?:\([A-Za-z0-9]+\))*'
    r')(?:\s*[:\-\—\.]\s*|\s+|$)',
    re.IGNORECASE
)


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
        pdf_page: Optional[int] = None,
        printed_page: Optional[str] = None,
        chunker_version: str = "1.0",
        embed_model: str = "bge-large"
    ):
        self.text = text
        self.raw_text = raw_text
        self.citation = citation
        self.header = header
        self.locator = locator
        self.page_number = page_number
        self.pdf_page = pdf_page if pdf_page is not None else page_number
        self.printed_page = printed_page
        self.chunk_index = chunk_index
        self.source_hash = source_hash
        self.doc_hash = doc_hash
        self.filename = filename
        self.bbox = bbox
        self.structured_locator = structured_locator or StructuredLocator.from_raw(
            page=page_number,
            pdf_page=self.pdf_page,
            printed_page=self.printed_page,
            locator_str=locator,
            header=header,
            bbox=bbox
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
            "pdf_page": self.pdf_page,
            "printed_page": self.printed_page,
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


def infer_heading_level(heading: str) -> int:
    """Infer hierarchical depth of a heading (1=Article/Chapter, 2=Section, 3=Subsection)."""
    h = heading.strip().lower()
    if h.startswith('#'):
        hashes = len(heading) - len(heading.lstrip('#'))
        return min(hashes, 4)
    if h.startswith(('article', 'art.', 'chapter', 'part', 'title', 'exhibit', 'schedule', 'appendix')):
        return 1
    if h.startswith(('section', 'sec.', '§', 'clause')) or re.match(r'^\d+\.\d+', h):
        if len(re.findall(r'\.\d+', h)) >= 2:
            return 3
        return 2
    if re.match(r'^\([a-z0-9]+\)', h) or h.startswith(('subsection', 'sub-section', 'paragraph')):
        return 3
    return 1 if heading.isupper() else 2


def update_heading_stack(stack: List[str], new_heading: str, level: Optional[int] = None) -> List[str]:
    """Maintain monotonic observed heading stack at appropriate hierarchical depth."""
    lvl = level or infer_heading_level(new_heading)
    idx = max(0, min(lvl - 1, len(stack)))
    return stack[:idx] + [new_heading]


def detect_header_candidate(line: str) -> Optional[str]:
    """
    Inspect whether a single line qualifies as a structural heading or section title.
    Enforces layout-aware features:
    - Rejects running headers/footers, page numbers, and bates stamps.
    - Rejects table-of-contents lines with dotted leaders.
    - Requires section patterns to be anchored at line start (prevents 'Article IV.' buried mid-paragraph).
    - Checks native markdown headers (# to ######).
    - Checks short all-caps titles without sentence punctuation.
    """
    clean = line.strip()
    if not clean or len(clean) > 120:
        return None

    # 1. Reject noise (page numbers, confidential banners, bates numbers, pacer headers)
    if NOISE_LINE_REGEX.match(clean):
        return None

    # 2. Reject Table of Contents lines (dotted leaders ending in page numbers)
    if TOC_LINE_REGEX.search(clean):
        return None

    # 3. Check Markdown heading (# to ######)
    if clean.startswith('#'):
        return re.sub(r'^#+\s*', '', clean).strip()

    # 4. Check Section / Article pattern (must be a genuine header, not buried mid-paragraph prose)
    m = SECTION_PATTERN.search(clean)
    if m:
        prefix = clean[:m.start()].strip()
        # Mid-paragraph rejection:
        # If prefix contains lowercase prose words or is long, it is buried mid-paragraph
        if prefix and (not prefix.isupper() or len(prefix) > 40):
            return None
        if len(clean) <= 100:
            return clean
        return m.group(0).rstrip(' :.-—').strip()

    # 5. Check All-Caps short title (no sentence-ending punctuation)
    if clean.isupper() and 4 < len(clean) < 80 and not any(p in clean for p in ['.', ';', '!', '?']):
        return clean.title()

    return None


def format_chunk_citation(
    filename: str,
    page_number: Optional[int],
    locator: Optional[str] = None,
    header: Optional[str] = None,
    pdf_page: Optional[int] = None,
    printed_page: Optional[str] = None
) -> str:
    """Format canonical citation string."""
    return format_citation(
        filename=filename,
        page_number=page_number,
        pdf_page=pdf_page,
        printed_page=printed_page,
        locator=locator,
        header=header
    )


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

    # Flatten all elements with provenance: (page_num, locator, text, confidence, char_start, char_end, is_header, bbox, printed_page)
    elements: List[Tuple[Optional[int], Optional[str], str, Optional[float], Optional[int], Optional[int], bool, Optional[List[float]], Optional[str]]] = []
    for p in pages:
        p_text = p.text
        if not p_text.strip():
            continue
        page_idx = p.pdf_page if getattr(p, "pdf_page", None) is not None else (p.index if p.index is not None else getattr(p, "page_number", None))
        printed_pg = getattr(p, "printed_page", None)
        p_conf = getattr(p, "confidence", None)
        block_map = {b.text.strip(): b.bbox for b in p.blocks if b.bbox and b.text.strip()}
        native_heading_set = {b.text.strip() for b in p.blocks if getattr(b, "block_type", "") == "heading"}
        noise_block_set = {b.text.strip() for b in p.blocks if getattr(b, "block_type", "") in ("header_footer", "bates_stamp", "exhibit_stamp", "fax_stamp")}

        curr_lines: List[str] = []
        c_start: Optional[int] = None
        c_end: Optional[int] = None
        curr_bboxes: List[List[float]] = []

        for match in re.finditer(r'[^\r\n]+', p_text):
            line = match.group(0).strip()
            if not line:
                continue
            # Filter noise lines so running footers and bates stamps never pollute chunks or become headers
            if line in noise_block_set or NOISE_LINE_REGEX.match(line):
                continue
            line_bbox = block_map.get(line)
            is_native = line in native_heading_set or line.startswith('#')
            hdr = line if is_native else detect_header_candidate(line)
            if hdr:
                if curr_lines:
                    comb_bbox = union_bboxes(curr_bboxes) if curr_bboxes else None
                    elements.append((page_idx, p.locator, '\n'.join(curr_lines), p_conf, c_start, c_end, False, comb_bbox, printed_pg))
                    curr_lines = []
                    curr_bboxes = []
                    c_start = None
                elements.append((page_idx, p.locator, line, p_conf, match.start(), match.end(), True, line_bbox, printed_pg))
            else:
                if not curr_lines:
                    c_start = match.start()
                curr_lines.append(line)
                if line_bbox:
                    curr_bboxes.append(line_bbox)
                c_end = match.end()

        if curr_lines:
            comb_bbox = union_bboxes(curr_bboxes) if curr_bboxes else None
            elements.append((page_idx, p.locator, '\n'.join(curr_lines), p_conf, c_start, c_end, False, comb_bbox, printed_pg))

    if not elements:
        return []

    current_items: List[Tuple[Optional[int], Optional[str], str, Optional[float], Optional[int], Optional[int], bool, Optional[List[float]], Optional[str]]] = []
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
        first_printed = primary_item[8] if len(primary_item) > 8 and primary_item[8] is not None else (current_items[0][8] if len(current_items[0]) > 8 else None)

        # Use breadcrumb stack
        loc = " > ".join(heading_stack) if (first_page is None and heading_stack) else first_loc
        c_hash = compute_chunk_hash(raw_chunk)
        cit_str = format_chunk_citation(
            filename,
            page_number=first_page,
            pdf_page=first_page,
            printed_page=first_printed,
            locator=loc,
            header=current_header
        )

        c_start = current_items[0][4]
        c_end = current_items[-1][5]
        item_confs = [x[3] for x in current_items if x[3] is not None]
        chunk_conf = sum(item_confs) / len(item_confs) if item_confs else None

        # Bounding box union
        item_bboxes = [x[7] for x in current_items if len(x) > 7 and x[7] is not None]
        chunk_bbox = union_bboxes(item_bboxes)

        chunk_meta = {**base_meta, "doc_type": resolved_doc_type}
        s_loc = StructuredLocator.from_raw(
            page=first_page,
            pdf_page=first_page,
            printed_page=first_printed,
            locator_str=loc,
            header=current_header,
            bbox=chunk_bbox
        )
        if heading_stack:
            s_loc.path = [f"Page {first_page}"] + list(heading_stack) if first_page is not None else list(heading_stack)

        chunk_obj = Chunk(
            text=raw_chunk,          # Embed raw text only!
            raw_text=raw_chunk,
            citation=cit_str,
            header=current_header,
            locator=loc,
            page_number=first_page,
            pdf_page=first_page,
            printed_page=first_printed,
            chunk_index=global_chunk_idx,
            source_hash=c_hash,      # Hashed on raw text only!
            doc_hash=file_hash,
            filename=filename,
            structured_locator=s_loc,
            metadata=chunk_meta,
            char_start=c_start,
            char_end=c_end,
            confidence=chunk_conf,
            bbox=chunk_bbox,
            chunker_version=chunker_version,
            embed_model=embed_model
        )
        chunk_obj.heading_path = list(s_loc.path)
        chunks.append(chunk_obj)
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

        # Flush on physical page boundary so hits on Page N strictly belong to Page N
        if current_items and current_items[0][0] is not None and page_num is not None and current_items[0][0] != page_num:
            flush_current_chunk(clear_overlap=True)

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
            heading_stack = update_heading_stack(heading_stack, title)
            current_header = title
            if is_operative:
                has_operative_in_chunk = True
        else:
            has_body_in_chunk = True

        para_len = len(para)

        if para_len > max_chars:
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', para) if s.strip()]
            s_offset = c_start or 0
            para_bbox = item[7] if len(item) > 7 else None
            para_printed = item[8] if len(item) > 8 else None
            for s in sentences:
                s_len = len(s)
                if current_len + s_len + 2 > max_chars and current_items:
                    flush_current_chunk()
                current_items.append((page_num, loc, s, conf, s_offset, s_offset + s_len, False, para_bbox, para_printed))
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
