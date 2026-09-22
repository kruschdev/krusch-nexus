"""
KruschNexus Minimal Hybrid Retrieval Engine (retrieve.py)
=========================================================
150-line hybrid search: vector ANN + FTS + RRF (k=60) + section boost.
Strictly workspace-isolated. Zero LLM calls in search path.
"""

import re
import hashlib
import logging
from typing import List, Optional, Tuple, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text

from .models import SearchHit, Citation
from .store import DocumentChunk, Workspace
from .exceptions import WorkspaceRequiredError

logger = logging.getLogger("krusch_nexus.retrieve")

SECTION_PATTERN = re.compile(r'(?:§+|Section|Sec\.|Article|Clause)\s*([0-9]+[A-Za-z0-9\.\-]*)', re.IGNORECASE)
_QUERY_EMBED_CACHE: Dict[str, List[float]] = {}


def hash_query(q: str) -> str:
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
    rrf_k: int = 60
) -> List[SearchHit]:
    """Execute hybrid vector + full-text search with strict workspace tenant key."""
    if not workspace_id or workspace_id <= 0:
        raise WorkspaceRequiredError("Search requires an explicit workspace ID.")
    if not query or not query.strip():
        return []

    q_str = query.strip()
    is_postgres = db.bind.dialect.name == "postgresql" if db.bind else False
    sec_match = SECTION_PATTERN.search(q_str)
    target_section = sec_match.group(0).strip() if sec_match else None
    section_number = sec_match.group(1).strip() if sec_match else None

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

    # 2. Vector ANN in workspace
    dense_results: List[Tuple[DocumentChunk, float]] = []
    if query_vector and is_postgres:
        vec_literal = "[" + ",".join(str(f) for f in query_vector) + "]"
        doc_filter = "AND doc_type = :doc_type" if doc_type else ""
        sql = f"""
            SELECT id, 1 - (embedding <=> '{vec_literal}'::vector) as sim
            FROM document_chunks
            WHERE workspace_id = :ws_id AND embedding IS NOT NULL {doc_filter}
            ORDER BY embedding <=> '{vec_literal}'::vector ASC LIMIT :d_lim;
        """
        params: Dict[str, Any] = {"ws_id": workspace_id, "d_lim": dense_limit}
        if doc_type:
            params["doc_type"] = doc_type
        try:
            rows = db.execute(text(sql), params).fetchall()
            ids = [r[0] for r in rows]
            chunk_map = {c.id: c for c in db.query(DocumentChunk).filter(DocumentChunk.id.in_(ids)).all()} if ids else {}
            for r in rows:
                if r[0] in chunk_map:
                    dense_results.append((chunk_map[r[0]], float(r[1])))
        except Exception as e:
            logger.debug(f"Dense search error: {e}")

    # 3. FTS in workspace
    sparse_results: List[Tuple[DocumentChunk, float]] = []
    if is_postgres:
        doc_filter = "AND doc_type = :doc_type" if doc_type else ""
        sql = f"""
            SELECT id, ts_rank_cd(tsv, plainto_tsquery('english', :q)) as r_score
            FROM document_chunks
            WHERE workspace_id = :ws_id AND tsv @@ plainto_tsquery('english', :q) {doc_filter}
            ORDER BY r_score DESC LIMIT :s_lim;
        """
        params = {"ws_id": workspace_id, "q": q_str, "s_lim": sparse_limit}
        if doc_type:
            params["doc_type"] = doc_type
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
        # SQLite token match fallback
        toks = [t.lower() for t in re.findall(r'\w+', q_str) if len(t) > 2]
        q_base = db.query(DocumentChunk).filter(DocumentChunk.workspace_id == workspace_id)
        if doc_type:
            q_base = q_base.filter(DocumentChunk.doc_type == doc_type)
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

    for rank, (c, sim) in enumerate(dense_results, 1):
        obj_map[c.id] = c
        d_scores[c.id] = sim
        rrf[c.id] = rrf.get(c.id, 0.0) + (1.0 / (rrf_k + rank))

    for rank, (c, scr) in enumerate(sparse_results, 1):
        obj_map[c.id] = c
        s_scores[c.id] = scr
        rrf[c.id] = rrf.get(c.id, 0.0) + (1.0 / (rrf_k + rank))

    if not rrf:
        fallback = db.query(DocumentChunk).filter(DocumentChunk.workspace_id == workspace_id).order_by(DocumentChunk.id.desc()).limit(limit).all()
        for idx, c in enumerate(fallback):
            obj_map[c.id] = c
            rrf[c.id] = 1.0 / (rrf_k + idx + 1)

    # 5. Section Boost
    if target_section or section_number:
        for c_id, chunk in obj_map.items():
            h_text = ((chunk.header or "") + " " + (chunk.locator or "")).lower()
            if (target_section and target_section.lower() in h_text) or (section_number and section_number.lower() in h_text):
                rrf[c_id] += 0.05

    # 6. Format SearchHit
    sorted_ids = sorted(rrf.keys(), key=lambda x: rrf[x], reverse=True)
    hits: List[SearchHit] = []
    for c_id in sorted_ids[:limit]:
        c = obj_map[c_id]
        cit = c.citation or Citation(filename=c.filename or "", page_number=c.page_number, locator=c.locator, header=c.header).formatted()
        hits.append(SearchHit(
            citation=cit,
            page_number=c.page_number,
            header=c.header,
            locator=c.locator,
            score=round(rrf[c_id], 5),
            text=c.content,
            document_id=c.document_id,
            chunk_id=c.id,
            filename=c.filename,
            workspace=ws_name,
            chunk_index=c.chunk_index,
            dense_score=d_scores.get(c_id),
            sparse_score=s_scores.get(c_id),
            source_hash=c.source_hash,
            file_hash=c.doc_hash
        ))
    return hits


# Backward-compatible alias
hybrid_search = retrieve
