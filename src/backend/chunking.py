import re
import hashlib
import logging
from typing import List, Dict, Any, Optional, Set
from .parsers import ParsedPage, Document

logger = logging.getLogger("krusch_nexus.chunking")

# Section heading patterns for legal and business documents
SECTION_PATTERN = re.compile(
    r'(?:§+|Section|Sec\.|Article|Clause)\s*([0-9]+[A-Za-z0-9\.\-]*(?:\s+[A-Za-z0-9\s,\-\'\"]{0,50})?)',
    re.IGNORECASE
)
MARKDOWN_HEADING_PATTERN = re.compile(r'^(#{1,6}\s+[^\n]+)', re.MULTILINE)


class Chunk:
    """Represents a structurally aware document chunk with strict provenance."""
    def __init__(
        self,
        text: str,
        raw_text: str,
        header: str,
        page_number: int,
        chunk_index: int,
        source_hash: str,
        doc_hash: str,
        filename: str,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.text = text
        self.raw_text = raw_text
        self.header = header
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
            "header": self.header,
            "page_number": self.page_number,
            "chunk_index": self.chunk_index,
            "source_hash": self.source_hash,
            "doc_hash": self.doc_hash,
            "filename": self.filename,
            "metadata": self.metadata
        }

    def to_llama_document(self) -> Document:
        """Convert chunk into a Document for vector indexing."""
        meta = {
            **self.metadata,
            "filename": self.filename,
            "doc_hash": self.doc_hash,
            "source_hash": self.source_hash,
            "page_number": self.page_number,
            "page_label": str(self.page_number),
            "chunk_index": self.chunk_index,
            "header": self.header
        }
        return Document(text=self.text, metadata=meta)


def compute_chunk_hash(text: str) -> str:
    """Compute SHA-256 hash of normalized chunk text."""
    clean = re.sub(r'\s+', ' ', text).strip()
    return hashlib.sha256(clean.encode('utf-8')).hexdigest()


def detect_header_candidate(line: str) -> Optional[str]:
    """Inspect whether a single line qualifies as a structural heading or section title."""
    clean = line.strip()
    if not clean or len(clean) > 120:
        return None

    # Check Markdown heading
    if clean.startswith('#'):
        return re.sub(r'^#+\s*', '', clean).strip()

    # Check Section / Article pattern
    sec_match = SECTION_PATTERN.search(clean)
    if sec_match:
        return sec_match.group(0).strip()

    # Check All-Caps short title (e.g., "TERMINATION OF TENANCY")
    if clean.isupper() and 4 < len(clean) < 80 and not any(p in clean for p in [".", ";", "!", "?"]):
        return clean.title()

    return None


def chunk_document_pages(
    pages: List[ParsedPage],
    filename: str,
    file_hash: str,
    max_chars: int = 2000,
    overlap_chars: int = 150,
    base_metadata: Optional[Dict[str, Any]] = None
) -> List[Chunk]:
    """
    Split multi-page document into structure-aware chunks.
    Preserves page boundaries, tracks section headings, and prepends context breadcrumbs.
    """
    chunks: List[Chunk] = []
    global_chunk_idx = 0
    active_header = "General"

    base_meta = dict(base_metadata or {})
    doc_type = base_meta.get("doc_type", "general")

    for page in pages:
        page_text = page.text.strip()
        if not page_text:
            continue

        # Split page into paragraphs preserving whitespace lines
        paragraphs = [p.strip() for p in re.split(r'\n\s*\n', page_text) if p.strip()]
        if not paragraphs:
            paragraphs = [page_text]

        current_block: List[str] = []
        current_len = 0

        for p in paragraphs:
            # Check for header update
            detected = detect_header_candidate(p.split('\n')[0])
            if detected:
                active_header = detected

            p_len = len(p)

            # If single paragraph exceeds max_chars, split along sentence boundaries
            if p_len > max_chars:
                sentences = re.split(r'(?<=[.!?])\s+', p)
                for s in sentences:
                    s_len = len(s)
                    if current_len + s_len + 1 > max_chars and current_block:
                        raw_chunk = "\n\n".join(current_block)
                        prefix = f"[{filename} - p.{page.page_number}] {active_header}"
                        full_chunk = f"{prefix}\n\n{raw_chunk}".strip()
                        c_hash = compute_chunk_hash(full_chunk)

                        chunk_meta = {**base_meta, "doc_type": doc_type}
                        chunks.append(Chunk(
                            text=full_chunk,
                            raw_text=raw_chunk,
                            header=active_header,
                            page_number=page.page_number,
                            chunk_index=global_chunk_idx,
                            source_hash=c_hash,
                            doc_hash=file_hash,
                            filename=filename,
                            metadata=chunk_meta
                        ))
                        global_chunk_idx += 1
                        current_block = [s]
                        current_len = s_len
                    else:
                        current_block.append(s)
                        current_len += s_len + 1
            elif current_len + p_len + 2 > max_chars and current_block:
                raw_chunk = "\n\n".join(current_block)
                prefix = f"[{filename} - p.{page.page_number}] {active_header}"
                full_chunk = f"{prefix}\n\n{raw_chunk}".strip()
                c_hash = compute_chunk_hash(full_chunk)

                chunk_meta = {**base_meta, "doc_type": doc_type}
                chunks.append(Chunk(
                    text=full_chunk,
                    raw_text=raw_chunk,
                    header=active_header,
                    page_number=page.page_number,
                    chunk_index=global_chunk_idx,
                    source_hash=c_hash,
                    doc_hash=file_hash,
                    filename=filename,
                    metadata=chunk_meta
                ))
                global_chunk_idx += 1
                current_block = [p]
                current_len = p_len
            else:
                current_block.append(p)
                current_len += p_len + 2

        if current_block:
            raw_chunk = "\n\n".join(current_block)
            prefix = f"[{filename} - p.{page.page_number}] {active_header}"
            full_chunk = f"{prefix}\n\n{raw_chunk}".strip()
            c_hash = compute_chunk_hash(full_chunk)

            chunk_meta = {**base_meta, "doc_type": doc_type}
            chunks.append(Chunk(
                text=full_chunk,
                raw_text=raw_chunk,
                header=active_header,
                page_number=page.page_number,
                chunk_index=global_chunk_idx,
                source_hash=c_hash,
                doc_hash=file_hash,
                filename=filename,
                metadata=chunk_meta
            ))
            global_chunk_idx += 1

    return chunks


def chunk_llama_documents(
    docs: List[Document],
    max_chars: int = 2000,
    overlap_chars: int = 150
) -> List[Chunk]:
    """
    Convenience function taking LlamaIndex Document objects and converting them into
    structure-aware chunks.
    """
    if not docs:
        return []

    first_meta = docs[0].metadata or {}
    filename = first_meta.get("filename", "unknown_document")
    file_hash = first_meta.get("file_hash", "")

    pages = []
    for d in docs:
        page_num = int(d.metadata.get("page_number") or d.metadata.get("page_label") or 1)
        pages.append(ParsedPage(
            page_number=page_num,
            text=d.text,
            has_images=d.metadata.get("has_images", False),
            ocr_applied=d.metadata.get("ocr_applied", False)
        ))

    return chunk_document_pages(
        pages=pages,
        filename=filename,
        file_hash=file_hash,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
        base_metadata=first_meta
    )


def deduplicate_chunks(chunks: List[Chunk], seen_hashes: Optional[Set[str]] = None) -> List[Chunk]:
    """Filter out duplicate chunks by exact source_hash."""
    seen = seen_hashes if seen_hashes is not None else set()
    unique = []
    for c in chunks:
        if c.source_hash not in seen:
            seen.add(c.source_hash)
            unique.append(c)
    return unique
