"""
KruschNexus Minimal Hybrid Retrieval Engine (retrieve.py)
=========================================================
150-line hybrid search: vector ANN + FTS + RRF (k=60) + statutory & phrase boost.
Strictly workspace-isolated. Zero LLM calls in search path.
"""

import re
import hashlib
import logging
from typing import List, Optional, Tuple, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text

from .models import SearchHit, Citation, StructuredLocator, NexusConfig
from .store import DocumentChunk, Workspace
from .exceptions import WorkspaceRequiredError

logger = logging.getLogger("krusch_nexus.retrieve")

SECTION_PATTERN = re.compile(
    r'(?:§+|Section|Sec\.|Article|Art\.|Clause)\s*([0-9IVXLCDM]+[A-Za-z0-9\.\-]*)',
    re.IGNORECASE
)
QUOTE_PATTERN = re.compile(r'"([^"]{3,})"')
_QUERY_EMBED_CACHE: Dict[str, List[float]] = {}


def hash_query(q: str) -> str:
    """Deterministic hash of search query string."""
    return hashlib.sha256(re.sub(r'\s+', ' ', q).strip().encode('utf-8')).hexdigest()


def retrieve(
    query: str,
    workspace_id: int,
    db: Session,
    embed_fn=None,
    doc_type: Optional[str] = None,
    limit: int = 5,
    dense_limit: int = 25,
    sparse_limit: int = 25,
    rrf_k: Optional[int] = None,
    section_boost_header: Optional[float] = None,
    section_boost_content: Optional[float] = None,
    phrase_boost: Optional[float] = None,
    filters: Optional[Dict[str, Any]] = None,
    config: Optional[NexusConfig] = None
) -> List[SearchHit]:
    """
    Execute hybrid vector + full-text search with strict workspace tenant key,
    exact quote phrase boosting, statutory section query parsing, explainability metadata,
    and librarian filter support.
    """
    if not workspace_id or workspace_id <= 0:
        raise WorkspaceRequiredError("Search requires an explicit workspace ID.")
    if not query or not query.strip():
        return []

    conf = config or NexusConfig.from_env()
    k_val = rrf_k if rrf_k is not None else conf.rrf_k
    boost_hdr = section_boost_header if section_boost_header is not None else conf.section_boost_header
    boost_cnt = section_boost_content if section_boost_content is not None else conf.section_boost_content
    boost_phrase = phrase_boost if phrase_boost is not None else conf.phrase_boost

    q_str = query.strip()
    is_postgres = db.bind.dialect.name == "postgresql" if db.bind else False

    # Extract legal citations (e.g. § 1950.5, Section 8.22.030, Art. IV)
    sec_match = SECTION_PATTERN.search(q_str)
    target_section = sec_match.group(0).strip() if sec_match else None
    section_number = sec_match.group(1).strip() if sec_match else None

    # Extract exact phrase quotes (e.g. "liquidated damages")
    quoted_phrases = [p.strip().lower() for p in QUOTE_PATTERN.findall(q_str) if len(p.strip()) >= 3]

    # Filters
    resolved_filters = dict(filters or {})
    active_doc_type = doc_type or resolved_filters.get("doc_type")
    filter_page = resolved_filters.get("page")
    filter_doc_id = resolved_filters.get("doc_id")
    filter_filename = resolved_filters.get("filename")
    filter_header_regex = resolved_filters.get("header_regex")

    ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    ws_name = ws.name if ws else f"Workspace_{workspace_id}"

    # 1. Embed Query (cached by hash)
    query_vector = None
    if embed_fn:
        q_h = hash_query(q_str)
        if q_h in _QUERY_EMBED_CACHE:
            query_vector = _QUERY_EMBED_CACHE[q_h]
        else:
            try:
                query_vector = embed_fn(q_str)
                if query_vector:
                    _QUERY_EMBED_CACHE[q_h] = query_vector
            except Exception as e:
                logger.warning(f"Query embed failed: {e}")

    # 2. Vector ANN in workspace (strictly respecting hard workspace_id tenant key)
    dense_results: List[Tuple[DocumentChunk, float]] = []
    if query_vector and is_postgres:
        vec_literal = "[" + ",".join(str(f) for f in query_vector) + "]"
        doc_filter = "AND doc_type = :doc_type" if active_doc_type else ""
        try:
            # Set HNSW ef_search runtime tuning parameter
            db.execute(text(f"SET LOCAL hnsw.ef_search = {conf.hnsw_ef_search};"))
        except Exception:
            pass

        sql = f"""
            SELECT id, 1 - (embedding <=> '{vec_literal}'::vector) as sim
            FROM document_chunks
            WHERE workspace_id = :ws_id AND embedding IS NOT NULL {doc_filter}
            ORDER BY embedding <=> '{vec_literal}'::vector ASC LIMIT :d_lim;
        """
        params: Dict[str, Any] = {"ws_id": workspace_id, "d_lim": dense_limit}
        if active_doc_type:
            params["doc_type"] = active_doc_type
        try:
            rows = db.execute(text(sql), params).fetchall()
            ids = [r[0] for r in rows]
            chunk_map = {c.id: c for c in db.query(DocumentChunk).filter(DocumentChunk.id.in_(ids)).all()} if ids else {}
            for r in rows:
                if r[0] in chunk_map:
                    dense_results.append((chunk_map[r[0]], float(r[1])))
        except Exception as e:
            logger.debug(f"Dense search error: {e}")

    # 3. FTS in workspace (strictly respecting hard workspace_id tenant key)
    sparse_results: List[Tuple[DocumentChunk, float]] = []
    if is_postgres:
        doc_filter = "AND doc_type = :doc_type" if active_doc_type else ""
        clean_fts_q = re.sub(r'["\'§]', ' ', q_str).strip()
        sql = f"""
            SELECT id, ts_rank_cd(to_tsvector('english', content), plainto_tsquery('english', :q)) as r_score
            FROM document_chunks
            WHERE workspace_id = :ws_id AND to_tsvector('english', content) @@ plainto_tsquery('english', :q) {doc_filter}
            ORDER BY r_score DESC LIMIT :s_lim;
        """
        params = {"ws_id": workspace_id, "q": clean_fts_q, "s_lim": sparse_limit}
        if active_doc_type:
            params["doc_type"] = active_doc_type
        try:
            rows = db.execute(text(sql), params).fetchall()
            ids = [r[0] for r in rows]
            chunk_map = {c.id: c for c in db.query(DocumentChunk).filter(DocumentChunk.id.in_(ids)).all()} if ids else {}
            for r in rows:
                if r[0] in chunk_map:
                    sparse_results.append((chunk_map[r[0]], float(r[1])))
        except Exception as e:
            logger.debug(f"Sparse FTS error: {e}")
    else:
        # SQLite lexical match fallback (strictly respecting hard workspace_id tenant key)
        toks = [t.lower() for t in re.findall(r'\w+', q_str) if len(t) > 2]
        q_base = db.query(DocumentChunk).filter(DocumentChunk.workspace_id == workspace_id)
        if active_doc_type:
            q_base = q_base.filter(DocumentChunk.doc_type == active_doc_type)
        scored = []
        for c in q_base.all():
            full = (c.content + " " + (c.header or "") + " " + (c.locator or "")).lower()
            m = sum(1 for t in toks if t in full)
            if m > 0:
                scored.append((c, float(m)))
        scored.sort(key=lambda x: x[1], reverse=True)
        sparse_results = scored[:sparse_limit]

    # 4. Reciprocal Rank Fusion (k=60)
    rrf: Dict[int, float] = {}
    obj_map: Dict[int, DocumentChunk] = {}
    d_scores: Dict[int, float] = {}
    s_scores: Dict[int, float] = {}
    v_ranks: Dict[int, int] = {}
    f_ranks: Dict[int, int] = {}
    sec_boosted: Dict[int, bool] = {}
    lex_boosted: Dict[int, bool] = {}
    phrase_boosted: Dict[int, bool] = {}

    for rank, (c, sim) in enumerate(dense_results, 1):
        obj_map[c.id] = c
        d_scores[c.id] = sim
        v_ranks[c.id] = rank
        rrf[c.id] = rrf.get(c.id, 0.0) + (1.0 / (k_val + rank))

    for rank, (c, scr) in enumerate(sparse_results, 1):
        obj_map[c.id] = c
        s_scores[c.id] = scr
        f_ranks[c.id] = rank
        rrf[c.id] = rrf.get(c.id, 0.0) + (1.0 / (k_val + rank))

    # Fallback if no vector/FTS match (strictly respecting hard workspace_id tenant key)
    if not rrf:
        fallback_query = db.query(DocumentChunk).filter(DocumentChunk.workspace_id == workspace_id)
        if active_doc_type:
            fallback_query = fallback_query.filter(DocumentChunk.doc_type == active_doc_type)
        fallback = fallback_query.order_by(DocumentChunk.id.desc()).limit(limit).all()
        for idx, c in enumerate(fallback):
            obj_map[c.id] = c
            rrf[c.id] = 1.0 / (k_val + idx + 1)

    # 5. Exact Quoted Phrase Boosting ("liquidated damages")
    if quoted_phrases:
        for c_id, chunk in obj_map.items():
            content_low = chunk.content.lower()
            header_low = ((chunk.header or "") + " " + (chunk.locator or "")).lower()
            for qp in quoted_phrases:
                if qp in content_low or qp in header_low:
                    rrf[c_id] += boost_phrase
                    phrase_boosted[c_id] = True

    # 6. Statutory Section Lexical-First Boost (§ 1950.5, Section 8.22.030, Art. IV)
    if target_section or section_number:
        target_token = (section_number or target_section or "").lower()
        for c_id, chunk in obj_map.items():
            h_text = ((chunk.header or "") + " " + (chunk.locator or "")).lower()
            c_text = chunk.content.lower()

            if target_token in h_text:
                rrf[c_id] += boost_hdr
                sec_boosted[c_id] = True
            elif target_token in c_text:
                rrf[c_id] += boost_cnt
                lex_boosted[c_id] = True

    # 7. Apply Librarian Hard Predicate Filters
    candidate_ids = list(rrf.keys())
    for c_id in candidate_ids:
        chunk = obj_map[c_id]
        if filter_page is not None and chunk.page_number != filter_page:
            rrf.pop(c_id, None)
            continue
        if filter_doc_id is not None and chunk.document_id != filter_doc_id:
            rrf.pop(c_id, None)
            continue
        if filter_filename is not None and chunk.filename != filter_filename:
            rrf.pop(c_id, None)
            continue
        if filter_header_regex:
            full_h = (chunk.header or "") + " " + (chunk.locator or "")
            if not re.search(filter_header_regex, full_h, re.IGNORECASE):
                rrf.pop(c_id, None)
                continue

    # 8. Deduplicate Near-Identical Chunks (same source_hash or >85% overlap)
    sorted_ids = sorted(rrf.keys(), key=lambda x: rrf[x], reverse=True)
    deduped_ids: List[int] = []
    seen_hashes: set = set()
    seen_words: List[set] = []

    for c_id in sorted_ids:
        chunk = obj_map[c_id]
        if chunk.source_hash in seen_hashes:
            continue

        c_words = set(chunk.content.lower().split()[:60])
        is_dup = False
        for prev_words in seen_words:
            if c_words and prev_words:
                overlap = len(c_words & prev_words) / float(min(len(c_words), len(prev_words)))
                if overlap > 0.85:
                    is_dup = True
                    break
        if is_dup:
            continue

        seen_hashes.add(chunk.source_hash)
        seen_words.append(c_words)
        deduped_ids.append(c_id)
        if len(deduped_ids) >= limit:
            break

    # 9. Format SearchHit with Explainability ("The Fuse")
    hits: List[SearchHit] = []
    for c_id in deduped_ids:
        c = obj_map[c_id]
        cit = c.citation or Citation(filename=c.filename or "", page_number=c.page_number, locator=c.locator, header=c.header).formatted()

        reasons = []
        if c_id in v_ranks:
            reasons.append(f"dense_rank_{v_ranks[c_id]}")
        if c_id in f_ranks:
            reasons.append(f"sparse_rank_{f_ranks[c_id]}")
        if sec_boosted.get(c_id):
            reasons.append("section_locator_match")
        if lex_boosted.get(c_id):
            reasons.append("statutory_token_boost")
        if phrase_boosted.get(c_id):
            reasons.append("quoted_phrase_match")

        struct_loc = StructuredLocator.from_raw(page=c.page_number, locator_str=c.locator, header=c.header)

        hits.append(SearchHit(
            citation=cit,
            page_number=c.page_number,
            header=c.header,
            locator=c.locator,
            structured_locator=struct_loc,
            score=round(rrf[c_id], 5),
            text=c.content,
            document_id=c.document_id,
            chunk_id=c.id,
            filename=c.filename,
            workspace=ws_name,
            chunk_index=c.chunk_index,
            dense_score=d_scores.get(c_id),
            sparse_score=s_scores.get(c_id),
            vector_rank=v_ranks.get(c_id),
            fts_rank=f_ranks.get(c_id),
            section_boost=sec_boosted.get(c_id, False),
            lexical_boost=lex_boosted.get(c_id, False),
            phrase_boost=phrase_boosted.get(c_id, False),
            match_reasons=reasons,
            char_start=getattr(c, "char_start", None),
            char_end=getattr(c, "char_end", None),
            confidence=getattr(c, "confidence", None),
            source_hash=c.source_hash,
            file_hash=c.doc_hash,
            doc_type=c.doc_type
        ))
    return hits


# Backward-compatible alias
hybrid_search = retrieve
