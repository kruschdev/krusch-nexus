"""
KruschNexus Minimal Hybrid Retrieval Engine (retrieve.py)
=========================================================
Hybrid search: vector ANN + FTS + RRF (k=60) + statutory & phrase boost.
Strictly workspace-isolated. Zero LLM calls in search path.
Records explainability metrics in optional search_traces table.
"""

import re
import json
import time
import hashlib
import logging
from collections import OrderedDict
from typing import List, Optional, Tuple, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text, or_

from .models import SearchHit, Citation, StructuredLocator, NexusConfig, format_citation
from .store import DocumentChunk, Workspace, SearchTrace, Document
from .exceptions import WorkspaceRequiredError, ModelDimensionDriftError, NexusError

logger = logging.getLogger("krusch_nexus.retrieve")

SECTION_PATTERN = re.compile(
    r'(?:§+|Section|Sec\.|Article|Art\.|Clause)\s*([0-9IVXLCDM]+[A-Za-z0-9\.\-]*)',
    re.IGNORECASE
)
QUOTE_PATTERN = re.compile(r'"([^"]{3,})"')


class BoundedLRUCache:
    """Bounded, process-local LRU cache for query vector embeddings."""
    def __init__(self, maxsize: int = 1000):
        self.maxsize = maxsize
        self._cache: OrderedDict[str, Any] = OrderedDict()

    def get(self, key: str) -> Optional[Any]:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def __contains__(self, key: str) -> bool:
        return key in self._cache

    def __getitem__(self, key: str) -> Any:
        val = self._cache[key]
        self._cache.move_to_end(key)
        return val

    def __setitem__(self, key: str, value: Any) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = value
        if len(self._cache) > self.maxsize:
            self._cache.popitem(last=False)

    def __len__(self) -> int:
        return len(self._cache)

    def clear(self) -> None:
        self._cache.clear()


_QUERY_EMBED_CACHE = BoundedLRUCache(maxsize=1000)


def hash_query(q: str) -> str:
    """Deterministic hash of search query string."""
    return hashlib.sha256(re.sub(r'\s+', ' ', q).strip().encode('utf-8')).hexdigest()


def normalize_citation_token(token: str) -> str:
    """
    Normalize statutory citation tokens:
    '§1950.5' / 'Section 1950.5' / 'sec. 1950.5' -> '1950.5'
    'Article IV' / 'Art. IV' -> 'article iv'
    """
    if not token:
        return ""
    t = token.lower().strip()
    t = re.sub(r'^(?:§+|section|sec\.|article|art\.|clause)\s*', '', t)
    return t.strip()


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
    mode: str = "hybrid",
    filters: Optional[Dict[str, Any]] = None,
    config: Optional[NexusConfig] = None
) -> List[SearchHit]:
    """
    Execute hybrid vector + full-text search with strict workspace tenant key,
    exact quote phrase boosting, statutory section query parsing, explainability metadata,
    and librarian filter support.
    """
    start_time = time.time()
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
    norm_target_token = normalize_citation_token(section_number or target_section or "")

    # Extract exact phrase quotes (e.g. "liquidated damages")
    quoted_phrases = [p.strip().lower() for p in QUOTE_PATTERN.findall(q_str) if len(p.strip()) >= 3]

    # Filters (SQL-level predicates)
    resolved_filters = dict(filters or {})
    active_doc_type = doc_type or resolved_filters.get("doc_type")
    filter_page = resolved_filters.get("page")
    filter_doc_id = resolved_filters.get("doc_id")
    filter_filename = resolved_filters.get("filename")
    filter_header_regex = resolved_filters.get("header_regex")

    # Safe validation of header_regex filter
    compiled_header_regex = None
    if filter_header_regex:
        if len(filter_header_regex) > 120:
            raise NexusError("header_regex exceeds maximum length of 120 characters")
        try:
            compiled_header_regex = re.compile(filter_header_regex, re.IGNORECASE)
        except re.error as err:
            raise NexusError(f"Invalid header_regex pattern: {err}")

    ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    ws_name = ws.name if ws else f"Workspace_{workspace_id}"

    # Build SQL filter clauses
    sql_filter_clauses = []
    sql_params: Dict[str, Any] = {"ws_id": workspace_id}
    include_superseded = resolved_filters.get("include_superseded", False)
    if not include_superseded:
        sql_filter_clauses.append("AND (is_superseded IS FALSE OR is_superseded IS NULL)")
    if active_doc_type:
        sql_filter_clauses.append("AND doc_type = :doc_type")
        sql_params["doc_type"] = active_doc_type
    if filter_page is not None:
        sql_filter_clauses.append("AND page_number = :filter_page")
        sql_params["filter_page"] = filter_page
    if filter_doc_id is not None:
        sql_filter_clauses.append("AND document_id = :filter_doc_id")
        sql_params["filter_doc_id"] = filter_doc_id
    if filter_filename is not None:
        sql_filter_clauses.append("AND filename = :filter_filename")
        sql_params["filter_filename"] = filter_filename

    sql_filter_str = " ".join(sql_filter_clauses)

    # 1. Embed Query (cached by hash)
    query_vector = None
    q_h = hash_query(q_str)
    if embed_fn and mode != "fts_only":
        if q_h in _QUERY_EMBED_CACHE:
            query_vector = _QUERY_EMBED_CACHE[q_h]
        else:
            try:
                query_vector = embed_fn(q_str)
            except Exception as e:
                logger.warning(f"Query embed failed: {e}")

    # Model Dimension Drift Guard
    if query_vector:
        expected_dim = conf.embedding_dim or 1024
        if len(query_vector) != expected_dim:
            raise ModelDimensionDriftError(
                f"Dimension drift detected: query embedding has {len(query_vector)}d, but configuration expects {expected_dim}d."
            )
        sample_doc = db.query(Document).filter(Document.workspace_id == workspace_id).first()
        if sample_doc and sample_doc.embedding_dim and len(query_vector) != sample_doc.embedding_dim:
            raise ModelDimensionDriftError(
                f"Dimension drift detected: query embedding has {len(query_vector)}d, but documents in workspace '{ws_name}' were embedded with {sample_doc.embedding_dim}d ({sample_doc.embedding_model}). Migration required."
            )
        # Store in cache only after passing drift validation
        if embed_fn and q_h not in _QUERY_EMBED_CACHE:
            _QUERY_EMBED_CACHE[q_h] = query_vector

    # 2. Vector ANN in workspace (parameterized :qvec::vector without string interpolation)
    dense_results: List[Tuple[DocumentChunk, float]] = []
    min_sim_threshold: float = 0.40
    if query_vector and mode != "fts_only":
        if is_postgres:
            vec_literal = "[" + ",".join(str(f) for f in query_vector) + "]"
            try:
                db.execute(text(f"SET LOCAL hnsw.ef_search = {conf.hnsw_ef_search};"))
            except Exception:
                pass

            sql = f"""
                SELECT id, 1 - (embedding <=> CAST(:qvec AS vector)) as sim
                FROM document_chunks
                WHERE workspace_id = :ws_id AND embedding IS NOT NULL {sql_filter_str}
                  AND 1 - (embedding <=> CAST(:qvec AS vector)) >= :min_sim
                ORDER BY embedding <=> CAST(:qvec AS vector) ASC LIMIT :d_lim;
            """
            params = dict(sql_params)
            params["d_lim"] = dense_limit
            params["qvec"] = vec_literal
            params["min_sim"] = min_sim_threshold
            try:
                rows = db.execute(text(sql), params).fetchall()
                ids = [r[0] for r in rows]
                chunk_map = {c.id: c for c in db.query(DocumentChunk).filter(DocumentChunk.id.in_(ids)).all()} if ids else {}
                for r in rows:
                    if r[0] in chunk_map:
                        dense_results.append((chunk_map[r[0]], float(r[1])))
            except Exception as e:
                logger.debug(f"Dense search error: {e}")
        else:
            # SQLite fallback: in-memory dot-product cosine similarity
            q_base = db.query(DocumentChunk).filter(DocumentChunk.workspace_id == workspace_id)
            if not include_superseded:
                q_base = q_base.filter(or_(DocumentChunk.is_superseded == False, DocumentChunk.is_superseded == None))
            if active_doc_type:
                q_base = q_base.filter(DocumentChunk.doc_type == active_doc_type)
            if filter_page is not None:
                q_base = q_base.filter(DocumentChunk.page_number == filter_page)
            if filter_doc_id is not None:
                q_base = q_base.filter(DocumentChunk.document_id == filter_doc_id)
            if filter_filename is not None:
                q_base = q_base.filter(DocumentChunk.filename == filter_filename)

            scored_dense = []
            for c in q_base.all():
                if c.embedding is not None:
                    emb = c.embedding
                    if isinstance(emb, str):
                        try:
                            emb = json.loads(emb)
                        except Exception:
                            continue
                    if isinstance(emb, (list, tuple)) and len(emb) == len(query_vector):
                        sim = sum(a * b for a, b in zip(query_vector, emb))
                        if sim >= min_sim_threshold:
                            scored_dense.append((c, float(sim)))
            scored_dense.sort(key=lambda x: x[1], reverse=True)
            dense_results = scored_dense[:dense_limit]

    # 3. FTS in workspace (utilizing stored tsv_content GIN index on PostgreSQL)
    sparse_results: List[Tuple[DocumentChunk, float]] = []
    if is_postgres and mode != "vector_only":
        clean_fts_q = re.sub(r'["\'§]', ' ', q_str).strip()
        sql = f"""
            SELECT id, ts_rank_cd(tsv_content, plainto_tsquery('english', :q)) as r_score
            FROM document_chunks
            WHERE workspace_id = :ws_id AND tsv_content @@ plainto_tsquery('english', :q) {sql_filter_str}
            ORDER BY r_score DESC LIMIT :s_lim;
        """
        params = dict(sql_params)
        params["q"] = clean_fts_q
        params["s_lim"] = sparse_limit
        try:
            rows = db.execute(text(sql), params).fetchall()
            ids = [r[0] for r in rows]
            chunk_map = {c.id: c for c in db.query(DocumentChunk).filter(DocumentChunk.id.in_(ids)).all()} if ids else {}
            for r in rows:
                if r[0] in chunk_map:
                    sparse_results.append((chunk_map[r[0]], float(r[1])))
        except Exception as e:
            logger.debug(f"Stored tsv_content FTS failed or column missing ({e}); falling back to dynamic to_tsvector")
            fallback_sql = f"""
                SELECT id, ts_rank_cd(to_tsvector('english', content), plainto_tsquery('english', :q)) as r_score
                FROM document_chunks
                WHERE workspace_id = :ws_id AND to_tsvector('english', content) @@ plainto_tsquery('english', :q) {sql_filter_str}
                ORDER BY r_score DESC LIMIT :s_lim;
            """
            try:
                rows = db.execute(text(fallback_sql), params).fetchall()
                ids = [r[0] for r in rows]
                chunk_map = {c.id: c for c in db.query(DocumentChunk).filter(DocumentChunk.id.in_(ids)).all()} if ids else {}
                for r in rows:
                    if r[0] in chunk_map:
                        sparse_results.append((chunk_map[r[0]], float(r[1])))
            except Exception as fe:
                logger.debug(f"Sparse FTS fallback error: {fe}")
    elif mode != "vector_only":
        # SQLite lexical match fallback with SQL-level filtering
        toks = [t.lower() for t in re.findall(r'\w+', q_str) if len(t) > 2]
        q_base = db.query(DocumentChunk).filter(DocumentChunk.workspace_id == workspace_id)
        if not include_superseded:
            q_base = q_base.filter(or_(DocumentChunk.is_superseded == False, DocumentChunk.is_superseded == None))
        if active_doc_type:
            q_base = q_base.filter(DocumentChunk.doc_type == active_doc_type)
        if filter_page is not None:
            q_base = q_base.filter(DocumentChunk.page_number == filter_page)
        if filter_doc_id is not None:
            q_base = q_base.filter(DocumentChunk.document_id == filter_doc_id)
        if filter_filename is not None:
            q_base = q_base.filter(DocumentChunk.filename == filter_filename)

        scored = []
        for c in q_base.all():
            full = (c.content + " " + (c.header or "") + " " + (c.locator or "")).lower()
            m = sum(1 for t in toks if t in full)
            if m > 0:
                scored.append((c, float(m)))
        scored.sort(key=lambda x: x[1], reverse=True)
        sparse_results = scored[:sparse_limit]

    # 4. Scoring / RRF
    rrf: Dict[int, float] = {}
    obj_map: Dict[int, DocumentChunk] = {}
    d_scores: Dict[int, float] = {}
    s_scores: Dict[int, float] = {}
    v_ranks: Dict[int, int] = {}
    f_ranks: Dict[int, int] = {}
    sec_boosted: Dict[int, bool] = {}
    lex_boosted: Dict[int, bool] = {}
    phrase_boosted: Dict[int, bool] = {}
    boost_accumulated: Dict[int, float] = {}

    if mode == "vector_only":
        for rank, (c, sim) in enumerate(dense_results, 1):
            obj_map[c.id] = c
            d_scores[c.id] = sim
            v_ranks[c.id] = rank
            rrf[c.id] = sim
    elif mode == "fts_only":
        for rank, (c, scr) in enumerate(sparse_results, 1):
            obj_map[c.id] = c
            s_scores[c.id] = scr
            f_ranks[c.id] = rank
            rrf[c.id] = scr
    else:
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

    # Return empty result if neither vector nor FTS matched (kill confident hallucinations)
    if not rrf:
        return []

    # 5. Exact Quoted Phrase Boosting ("liquidated damages")
    MAX_TOTAL_BOOST = 0.12  # Capped boosts so section mention cannot drown better semantic hit
    if mode in ("hybrid", "rrf_boosts"):
        if quoted_phrases:
            for c_id, chunk in obj_map.items():
                content_low = chunk.content.lower()
                header_low = ((chunk.header or "") + " " + (chunk.locator or "")).lower()
                for qp in quoted_phrases:
                    if qp in content_low or qp in header_low:
                        add_b = min(boost_phrase, MAX_TOTAL_BOOST - boost_accumulated.get(c_id, 0.0))
                        if add_b > 0:
                            rrf[c_id] += add_b
                            boost_accumulated[c_id] = boost_accumulated.get(c_id, 0.0) + add_b
                        phrase_boosted[c_id] = True

        # 6. Normalized Section Boost (§ 1950.5, Section 8.22.030, Art. IV)
        if norm_target_token:
            for c_id, chunk in obj_map.items():
                # Extract heading tokens from heading_path or header
                h_tokens = []
                if chunk.heading_path:
                    try:
                        h_list = json.loads(chunk.heading_path) if isinstance(chunk.heading_path, str) else chunk.heading_path
                        for h in h_list:
                            h_tokens.append(normalize_citation_token(h))
                            h_tokens.append(h.lower())
                    except Exception:
                        pass

                h_text = ((chunk.header or "") + " " + (chunk.locator or "")).lower()
                h_norm = normalize_citation_token(h_text)
                c_text = chunk.content.lower()

                matched_header = (norm_target_token in h_tokens) or (norm_target_token in h_norm) or (norm_target_token in h_text)
                if matched_header:
                    add_b = min(boost_hdr, MAX_TOTAL_BOOST - boost_accumulated.get(c_id, 0.0))
                    if add_b > 0:
                        rrf[c_id] += add_b
                        boost_accumulated[c_id] = boost_accumulated.get(c_id, 0.0) + add_b
                    sec_boosted[c_id] = True
                elif norm_target_token in c_text:
                    add_b = min(boost_cnt, MAX_TOTAL_BOOST - boost_accumulated.get(c_id, 0.0))
                    if add_b > 0:
                        rrf[c_id] += add_b
                        boost_accumulated[c_id] = boost_accumulated.get(c_id, 0.0) + add_b
                    lex_boosted[c_id] = True

    # 7. Post-filter for header_regex (if specified)
    if compiled_header_regex:
        candidate_ids = list(rrf.keys())
        for c_id in candidate_ids:
            chunk = obj_map[c_id]
            full_h = (chunk.header or "") + " " + (chunk.locator or "")
            if not compiled_header_regex.search(full_h):
                rrf.pop(c_id, None)

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
        span = (c.char_start, c.char_end) if (c.char_start is not None and c.char_end is not None) else None
        struct_loc = StructuredLocator.from_raw(page=c.page_number, locator_str=c.locator, header=c.header, char_span=span)
        cit = c.citation or format_citation(filename=c.filename or "", page_number=c.page_number, locator=c.locator, header=c.header, structured_locator=struct_loc)

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

        h_path = []
        if getattr(c, "heading_path", None):
            try:
                h_path = json.loads(c.heading_path) if isinstance(c.heading_path, str) else c.heading_path
            except Exception:
                h_path = list(struct_loc.path)
        else:
            h_path = list(struct_loc.path)

        score_vec = {
            "dense_score": d_scores.get(c_id),
            "sparse_score": s_scores.get(c_id),
            "vector_rank": v_ranks.get(c_id),
            "fts_rank": f_ranks.get(c_id),
            "section_boost": sec_boosted.get(c_id, False),
            "phrase_boost": phrase_boosted.get(c_id, False),
            "final_score": round(rrf[c_id], 5)
        }
        logger.debug(f"Search hit chunk {c_id} ({c.filename}) score vector: {score_vec}")

        hits.append(SearchHit(
            citation=cit,
            page_number=c.page_number,
            header=c.header,
            locator=c.locator,
            structured_locator=struct_loc,
            heading_path=h_path,
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
            doc_type=c.doc_type,
            score_vector=score_vec
        ))

    # 10. Record Explainability Fuse into optional SearchTrace table
    try:
        dur_ms = round((time.time() - start_time) * 1000, 2)
        trace_rec = SearchTrace(
            workspace_id=workspace_id,
            query_hash=hash_query(q_str),
            dense_ranks=json.dumps(v_ranks),
            sparse_ranks=json.dumps(f_ranks),
            fused_ranks=json.dumps({c_id: rank for rank, c_id in enumerate(deduped_ids, 1)}),
            boosts_applied=json.dumps({
                c_id: [
                    b for b, flag in [
                        ("section_boost", sec_boosted.get(c_id)),
                        ("lexical_boost", lex_boosted.get(c_id)),
                        ("phrase_boost", phrase_boosted.get(c_id))
                    ] if flag
                ]
                for c_id in deduped_ids
            }),
            duration_ms=dur_ms
        )
        db.add(trace_rec)
        db.commit()
    except Exception as e:
        logger.debug(f"Search trace recording skipped: {e}")

    return hits


# Backward-compatible alias
hybrid_search = retrieve
