"""
KruschNexus Hybrid Retrieval Engine
===================================
Air-gapped, citation-first hybrid search combining pgvector dense cosine
similarity with PostgreSQL tsvector full-text search merged via Reciprocal
Rank Fusion (RRF) with structural section boosts.

Zero cloud dependencies. No LlamaIndex, no LangGraph, no external API egress.
"""

import re
import json
import logging
from typing import List, Optional, Dict, Any, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import text

from .models import SearchHit
from .db import DocumentChunk, Workspace

logger = logging.getLogger("krusch_nexus.rag_engine")

SECTION_QUERY_PATTERN = re.compile(
    r'(?:§+|Section|Sec\.|Article|Clause)\s*([0-9]+[A-Za-z0-9\.\-]*)',
    re.IGNORECASE
)


def format_citation(filename: str, page_number: int, header: Optional[str] = None) -> str:
    """Format canonical citation string, e.g. '[lease.pdf, p. 14, § 8.22]'."""
    if header and header.strip():
        h = header.strip()
        # Prepend § if header starts with numbers or Section
        if not h.startswith("§") and not h.startswith("#"):
            citation_header = f"§ {h}" if not h.lower().startswith("section") else h
        else:
            citation_header = h.lstrip("#").strip()
        return f"[{filename}, p. {page_number}, {citation_header}]"
    return f"[{filename}, p. {page_number}]"


def hybrid_search(
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
    """
    Execute hybrid vector + full-text search with strict workspace isolation.
    Merges dense and sparse ranks via Reciprocal Rank Fusion (RRF) and applies
    section/header pattern boosts for statutory/contractual citations.
    """
    if not query or not query.strip():
        return []

    query_str = query.strip()
    is_postgres = db.bind.dialect.name == "postgresql"

    # 1. Detect section/heading pattern in query (e.g. "§ 1950.5", "Section 8.22")
    sec_match = SECTION_QUERY_PATTERN.search(query_str)
    target_section = sec_match.group(0).strip() if sec_match else None
    section_number = sec_match.group(1).strip() if sec_match else None

    # Resolve workspace name
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    workspace_name = ws.name if ws else f"Workspace_{workspace_id}"

    # 2. Dense Vector Retrieval
    dense_results: List[Tuple[DocumentChunk, float]] = []
    query_vector = None
    if embed_fn:
        try:
            query_vector = embed_fn(query_str)
        except Exception as e:
            logger.warning(f"Embedding generation failed for query: {e}")

    if query_vector and is_postgres:
        vec_literal = "[" + ",".join(str(f) for f in query_vector) + "]"
        doc_type_filter = "AND doc_type = :doc_type" if doc_type else ""
        sql_dense = f"""
            SELECT id, 1 - (embedding <=> '{vec_literal}'::vector) as similarity
            FROM document_chunks
            WHERE workspace_id = :ws_id
              AND embedding IS NOT NULL
              {doc_type_filter}
            ORDER BY embedding <=> '{vec_literal}'::vector ASC
            LIMIT :d_limit;
        """
        params = {"ws_id": workspace_id, "d_limit": dense_limit}
        if doc_type:
            params["doc_type"] = doc_type

        try:
            rows = db.execute(text(sql_dense), params).fetchall()
            chunk_ids = [r[0] for r in rows]
            chunks_by_id = {
                c.id: c for c in db.query(DocumentChunk).filter(DocumentChunk.id.in_(chunk_ids)).all()
            } if chunk_ids else {}
            for r in rows:
                c_id, sim = r[0], float(r[1])
                if c_id in chunks_by_id:
                    dense_results.append((chunks_by_id[c_id], sim))
        except Exception as e:
            logger.error(f"Dense vector search error: {e}")

    # 3. Sparse Full-Text Search
    sparse_results: List[Tuple[DocumentChunk, float]] = []
    if is_postgres:
        doc_type_filter = "AND doc_type = :doc_type" if doc_type else ""
        sql_sparse = f"""
            SELECT id, ts_rank_cd(tsv, plainto_tsquery('english', :q)) as rank_score
            FROM document_chunks
            WHERE workspace_id = :ws_id
              AND tsv @@ plainto_tsquery('english', :q)
              {doc_type_filter}
            ORDER BY rank_score DESC
            LIMIT :s_limit;
        """
        params = {"ws_id": workspace_id, "q": query_str, "s_limit": sparse_limit}
        if doc_type:
            params["doc_type"] = doc_type

        try:
            rows = db.execute(text(sql_sparse), params).fetchall()
            chunk_ids = [r[0] for r in rows]
            chunks_by_id = {
                c.id: c for c in db.query(DocumentChunk).filter(DocumentChunk.id.in_(chunk_ids)).all()
            } if chunk_ids else {}
            for r in rows:
                c_id, score = r[0], float(r[1])
                if c_id in chunks_by_id:
                    sparse_results.append((chunks_by_id[c_id], score))
        except Exception as e:
            logger.debug(f"PostgreSQL sparse FTS search error: {e}")
    else:
        # SQLite Fallback: Keyword token matching & text substring
        tokens = [t.lower() for t in re.findall(r'\w+', query_str) if len(t) > 2]
        query_base = db.query(DocumentChunk).filter(DocumentChunk.workspace_id == workspace_id)
        if doc_type:
            query_base = query_base.filter(DocumentChunk.doc_type == doc_type)
        all_chunks = query_base.all()

        scored = []
        for c in all_chunks:
            c_text_lower = (c.content + " " + (c.header or "")).lower()
            match_count = sum(1 for tok in tokens if tok in c_text_lower)
            if match_count > 0:
                scored.append((c, float(match_count)))
        scored.sort(key=lambda x: x[1], reverse=True)
        sparse_results = scored[:sparse_limit]

    # 4. Reciprocal Rank Fusion (RRF)
    # RRF(d) = sum(1 / (k + rank))
    rrf_scores: Dict[int, float] = {}
    chunk_map: Dict[int, DocumentChunk] = {}
    dense_scores: Dict[int, float] = {}
    sparse_scores: Dict[int, float] = {}

    for rank, (chunk, sim) in enumerate(dense_results, start=1):
        c_id = chunk.id
        chunk_map[c_id] = chunk
        dense_scores[c_id] = sim
        rrf_scores[c_id] = rrf_scores.get(c_id, 0.0) + (1.0 / (rrf_k + rank))

    for rank, (chunk, score) in enumerate(sparse_results, start=1):
        c_id = chunk.id
        chunk_map[c_id] = chunk
        sparse_scores[c_id] = score
        rrf_scores[c_id] = rrf_scores.get(c_id, 0.0) + (1.0 / (rrf_k + rank))

    # If both dense and sparse returned 0 (e.g. SQLite without embeddings), fallback to recent chunks
    if not rrf_scores:
        fallback_query = db.query(DocumentChunk).filter(
            DocumentChunk.workspace_id == workspace_id
        )
        if doc_type:
            fallback_query = fallback_query.filter(DocumentChunk.doc_type == doc_type)
        fallback_chunks = fallback_query.order_by(DocumentChunk.id.desc()).limit(limit).all()
        for idx, c in enumerate(fallback_chunks):
            chunk_map[c.id] = c
            rrf_scores[c.id] = 1.0 / (rrf_k + idx + 1)

    # 5. Section / Header Boost
    for c_id, chunk in chunk_map.items():
        if target_section or section_number:
            header_clean = (chunk.header or "").lower()
            content_snippet = chunk.content[:200].lower()

            match_found = False
            if target_section and target_section.lower() in header_clean:
                match_found = True
            elif section_number and section_number.lower() in header_clean:
                match_found = True
            elif target_section and target_section.lower() in content_snippet:
                match_found = True

            if match_found:
                # Direct heading match gets a strong RRF boost equivalent to top rank
                rrf_scores[c_id] += 0.05

    # 6. Rank by final score and build canonical SearchHit models
    sorted_chunk_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
    hits: List[SearchHit] = []

    for c_id in sorted_chunk_ids[:limit]:
        chunk = chunk_map[c_id]
        cit = format_citation(chunk.filename, chunk.page_number, chunk.header)
        hits.append(SearchHit(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            workspace=workspace_name,
            filename=chunk.filename,
            page_number=chunk.page_number,
            chunk_index=chunk.chunk_index,
            header=chunk.header,
            content=chunk.content,
            citation=cit,
            dense_score=dense_scores.get(c_id),
            sparse_score=sparse_scores.get(c_id),
            rrf_score=round(rrf_scores[c_id], 5),
            source_hash=chunk.source_hash,
            file_hash=chunk.doc_hash
        ))

    return hits
