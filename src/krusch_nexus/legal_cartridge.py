"""
KruschNexus Legal Authority Cartridge Retriever (legal_cartridge.py)
=====================================================================
High-performance hybrid (FTS5 BM25 + 1024-dim Vector Dot Product) retrieval
interface over the standalone KruschNexus Legal Authority Cartridge SQLite database.

Enables instant sub-50ms case law and municipal code retrieval for:
- KruschLaw (precedents, defenses, judicial interpretations)
- KruschBiz / KruschBizLaw ("The Join" contract compliance checks)
- KruschGateway MCP (ask_law and check_compliance verbs)
"""

import os
import re
import math
import json
import time
import struct
import sqlite3
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

import httpx

logger = logging.getLogger("krusch_nexus.legal_cartridge")

DEFAULT_CARTRIDGE_PATHS = [
    Path("/mnt/media1/krusch-nexus/cartridges/cases_nexus_authority.sqlite"),
    Path(__file__).resolve().parent.parent.parent / "data" / "cartridges" / "cases_nexus_authority.sqlite",
    Path.home() / "homelab" / "projects" / "krusch-nexus" / "data" / "cartridges" / "cases_nexus_authority.sqlite"
]

DEFAULT_EMBED_URL = os.getenv("OLLAMA_EMBED_HOST", "http://127.0.0.1:11434")
DEFAULT_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "snowflake-arctic-embed2")

_FTS5_QUERY_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "these", "those",
    "what", "when", "where", "which", "who", "whom", "whose", "why", "how",
    "are", "was", "were", "been", "being", "have", "has", "had",
    "does", "did", "doing", "will", "would", "could", "should", "can", "may", "might", "must", "shall",
    "into", "onto", "upon", "about", "under", "over", "between", "through", "during", "before", "after",
    "not", "nor", "but", "or", "near", "any", "all", "each", "every", "some", "such", "other",
    "than", "then", "there", "their", "theirs", "they", "them", "its", "your", "our"
})


def build_safe_fts5_match_query(query: str, max_terms: int = 28) -> str:
    """
    Sanitize a natural-language or citation query into a crash-proof SQLite FTS5 MATCH expression.

    - Extracts alphanumeric tokens only (stripping ?, ", ', (, ), *, :, -, §, etc.)
    - Lowercases and double-quotes every token ('"token"') so uppercase FTS5 boolean/proximity
      keywords (AND, OR, NOT, NEAR) are treated strictly as phrase literals rather than operators
    - Filters high-frequency query stopwords while falling back cleanly when a query contains
      only stopwords/short tokens, or returning "" when zero alphanumeric tokens exist
    """
    raw_tokens = [tok.lower() for tok in re.findall(r"[A-Za-z0-9]+", query or "")]
    if not raw_tokens:
        return ""

    seen = set()
    informative: List[str] = []
    fallback: List[str] = []

    for tok in raw_tokens:
        if tok in seen:
            continue
        seen.add(tok)
        if len(tok) >= 2 or tok.isdigit():
            fallback.append(tok)
            if tok not in _FTS5_QUERY_STOPWORDS:
                informative.append(tok)

    selected = informative if informative else (fallback if fallback else list(dict.fromkeys(raw_tokens)))
    limit_terms = max(1, int(max_terms or 28))
    if len(selected) > limit_terms:
        # Prioritize numeric/statutory identifiers and longer content words when truncating long queries
        selected = sorted(
            selected,
            key=lambda t: (1 if any(ch.isdigit() for ch in t) else 0, len(t)),
            reverse=True
        )[:limit_terms]

    return " OR ".join(f'"{tok}"' for tok in selected)


def _safe_cosine_similarity(q_vec: List[float], doc_vec: tuple | List[float]) -> float:
    """Compute cosine similarity safely across unit-normalized or unnormalized vectors."""
    if not q_vec or not doc_vec:
        return 0.0
    n = min(len(q_vec), len(doc_vec))
    dot = 0.0
    q_norm_sq = 0.0
    d_norm_sq = 0.0
    for i in range(n):
        qv = q_vec[i]
        dv = doc_vec[i]
        dot += qv * dv
        q_norm_sq += qv * qv
        d_norm_sq += dv * dv
    if q_norm_sq <= 0.0 or d_norm_sq <= 0.0:
        return 0.0
    return dot / math.sqrt(q_norm_sq * d_norm_sq)


class LegalAuthorityCartridge:
    """Client for high-speed hybrid retrieval over the KruschNexus Legal Cartridge."""

    def __init__(
        self,
        db_path: Optional[str | Path] = None,
        embed_url: str = DEFAULT_EMBED_URL,
        embed_fn: Optional[Any] = None,
    ):
        self.db_path = self._resolve_db_path(db_path)
        self.embed_url = embed_url.rstrip("/")
        self.embed_fn = embed_fn
        self.embed_model = DEFAULT_EMBED_MODEL
        if not self.db_path or not self.db_path.exists():
            raise FileNotFoundError(f"Legal Authority Cartridge SQLite file not found: {self.db_path}")

        # Verify database connection & bind query model to cartridge manifest (INV-05)
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM documents;")
            self.doc_count = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM chunks;")
            self.chunk_count = cur.fetchone()[0]
            try:
                cur.execute("SELECT embed_model FROM cartridge_manifest LIMIT 1;")
                mrow = cur.fetchone()
                if mrow and mrow[0]:
                    self.embed_model = str(mrow[0]).strip()
            except sqlite3.OperationalError:
                pass
            logger.info(
                f"Loaded Legal Authority Cartridge: {self.db_path} "
                f"({self.doc_count} docs, {self.chunk_count} chunks, model={self.embed_model})"
            )
        finally:
            conn.close()

    def _resolve_db_path(self, db_path: Optional[str | Path]) -> Optional[Path]:
        if db_path:
            p = Path(db_path).resolve()
            if p.exists():
                return p
        for candidate in DEFAULT_CARTRIDGE_PATHS:
            if candidate.exists():
                return candidate
        return None

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA cache_size = -32000;")
        return conn

    def _embed_query(self, query: str) -> List[float]:
        """Embed search query using the cartridge's exact recorded embedding space."""
        if self.embed_fn is not None:
            return [float(x) for x in self.embed_fn(query)]
        url = f"{self.embed_url}/api/embed"
        formatted_q = f"query: {query}" if self.embed_model.startswith("snowflake-arctic-embed2") and not query.startswith("query: ") else query
        keep_alive = -1 if self.embed_model.startswith("snowflake-arctic-embed2") else "5m"
        payload = {
            "model": self.embed_model,
            "input": [formatted_q],
            "truncate": True,
            "keep_alive": keep_alive
        }
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["embeddings"][0]

    def search(
        self,
        query: str,
        limit: int = 5,
        jurisdiction: Optional[str] = None,
        source_table: Optional[str] = None,
        candidate_pool_size: int = 300,
        rerank: bool = False,
        rerank_model: Optional[str] = None,
        rerank_candidates: int = 16,
        rerank_fn: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute sub-50ms hybrid BM25 + dense vector retrieval (with optional local CrossEncoder reranking):
        1. FTS5 pre-selects top BM25-ranked candidate chunks matching quoted query keywords.
        2. If FTS yields candidates, rerank candidates by vector cosine similarity + BM25 RRF tie-break.
        3. If FTS yields 0 candidates (out-of-vocabulary or punctuation-only query), fall back to vector scan.
        4. When `rerank=True` (or `rerank_fn` is provided), score the top `rerank_candidates` hits with
           contextual Document + Section provenance via `compute_local_rerank_scores` and prune to `limit`.
        """
        t0 = time.time()
        pool_size = max(1, int(candidate_pool_size or 300))
        top_limit = max(1, int(limit or 5))
        conn = self._get_conn()
        cur = conn.cursor()

        try:
            fts_match_expr = build_safe_fts5_match_query(query)
            candidates = []

            # 1. Fetch BM25-ranked candidate chunks via FTS5 (eliminating insertion-order starvation)
            if fts_match_expr:
                if not jurisdiction and not source_table:
                    sql_fts = """
                        WITH ranked_fts AS (
                            SELECT chunk_id, bm25(chunks_fts) AS fts_bm25
                            FROM chunks_fts
                            WHERE chunks_fts MATCH ?
                            ORDER BY bm25(chunks_fts) ASC
                            LIMIT ?
                        )
                        SELECT
                            c.chunk_id,
                            c.doc_id,
                            c.chunk_index,
                            c.citation,
                            c.heading_path,
                            c.locator,
                            c.text,
                            c.source_hash,
                            c.char_start,
                            c.char_end,
                            c.embedding,
                            d.title,
                            d.jurisdiction,
                            d.court,
                            d.decision_year,
                            d.source_table,
                            ranked_fts.fts_bm25
                        FROM ranked_fts
                        JOIN chunks c ON ranked_fts.chunk_id = c.chunk_id
                        JOIN documents d ON c.doc_id = d.id
                        ORDER BY ranked_fts.fts_bm25 ASC;
                    """
                    cur.execute(sql_fts, [fts_match_expr, pool_size])
                else:
                    sql_fts = """
                        SELECT
                            c.chunk_id,
                            c.doc_id,
                            c.chunk_index,
                            c.citation,
                            c.heading_path,
                            c.locator,
                            c.text,
                            c.source_hash,
                            c.char_start,
                            c.char_end,
                            c.embedding,
                            d.title,
                            d.jurisdiction,
                            d.court,
                            d.decision_year,
                            d.source_table,
                            bm25(chunks_fts) AS fts_bm25
                        FROM chunks_fts
                        JOIN chunks c ON chunks_fts.chunk_id = c.chunk_id
                        JOIN documents d ON c.doc_id = d.id
                        WHERE chunks_fts MATCH ?
                    """
                    params: List[Any] = [fts_match_expr]
                    if jurisdiction:
                        sql_fts += " AND d.jurisdiction LIKE ?"
                        params.append(f"%{jurisdiction}%")
                    if source_table:
                        sql_fts += " AND d.source_table = ?"
                        params.append(source_table)
                    sql_fts += " ORDER BY bm25(chunks_fts) ASC LIMIT ?;"
                    params.append(pool_size)
                    cur.execute(sql_fts, params)

                candidates = cur.fetchall()

            # 2. Embed query vector
            q_vec = self._embed_query(query)

            scored_hits: List[Dict[str, Any]] = []
            if candidates:
                ranked_pairs = []
                for fts_rank, r in enumerate(candidates):
                    emb_blob = r["embedding"]
                    dim = len(emb_blob) // 4
                    vec = struct.unpack(f"{dim}f", emb_blob)
                    sim = _safe_cosine_similarity(q_vec, vec)
                    rrf_bonus = 0.05 * (60.0 / (60.0 + fts_rank))
                    heading = json.loads(r["heading_path"]) if r["heading_path"] else []
                    hit = {
                        "chunk_id": r["chunk_id"],
                        "doc_id": r["doc_id"],
                        "chunk_index": r["chunk_index"],
                        "citation": r["citation"],
                        "title": r["title"],
                        "heading_path": heading,
                        "locator": r["locator"],
                        "text": r["text"],
                        "jurisdiction": r["jurisdiction"],
                        "court": r["court"],
                        "decision_year": r["decision_year"],
                        "source_table": r["source_table"],
                        "char_start": r["char_start"],
                        "char_end": r["char_end"],
                        "score": round(sim, 4)
                    }
                    ranked_pairs.append((sim + rrf_bonus, sim, -fts_rank, hit))

                ranked_pairs.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
                scored_hits = [item[3] for item in ranked_pairs]
            else:
                # Fallback: scan filtered pool if FTS yielded zero lexical matches
                sql_fallback = """
                    SELECT
                        c.chunk_id, c.doc_id, c.chunk_index, c.citation, c.heading_path,
                        c.locator, c.text, c.char_start, c.char_end, c.embedding,
                        d.title, d.jurisdiction, d.court, d.decision_year, d.source_table
                    FROM chunks c
                    JOIN documents d ON c.doc_id = d.id
                    WHERE 1=1
                """
                fb_params: List[Any] = []
                if jurisdiction:
                    sql_fallback += " AND d.jurisdiction LIKE ?"
                    fb_params.append(f"%{jurisdiction}%")
                if source_table:
                    sql_fallback += " AND d.source_table = ?"
                    fb_params.append(source_table)
                sql_fallback += " LIMIT ?;"
                fb_params.append(pool_size)

                cur.execute(sql_fallback, fb_params)
                for r in cur.fetchall():
                    emb_blob = r["embedding"]
                    dim = len(emb_blob) // 4
                    vec = struct.unpack(f"{dim}f", emb_blob)
                    sim = _safe_cosine_similarity(q_vec, vec)
                    heading = json.loads(r["heading_path"]) if r["heading_path"] else []
                    scored_hits.append({
                        "chunk_id": r["chunk_id"],
                        "doc_id": r["doc_id"],
                        "chunk_index": r["chunk_index"],
                        "citation": r["citation"],
                        "title": r["title"],
                        "heading_path": heading,
                        "locator": r["locator"],
                        "text": r["text"],
                        "jurisdiction": r["jurisdiction"],
                        "court": r["court"],
                        "decision_year": r["decision_year"],
                        "source_table": r["source_table"],
                        "char_start": r["char_start"],
                        "char_end": r["char_end"],
                        "score": round(sim, 4)
                    })
                scored_hits.sort(key=lambda x: x["score"], reverse=True)

            if (rerank or rerank_fn is not None) and scored_hits:
                from .retrieve import (
                    DEFAULT_RERANKER_MODEL,
                    compute_local_rerank_scores,
                    format_chunk_for_reranking,
                )

                rerank_pool_n = max(top_limit, int(rerank_candidates or 16))
                target_slice = scored_hits[:rerank_pool_n]
                formatted_passages = [
                    format_chunk_for_reranking(
                        text=h["text"],
                        filename=h.get("title") or h.get("citation"),
                        locator=h.get("locator"),
                        heading_path=h.get("heading_path"),
                    )
                    for h in target_slice
                ]
                ce_scores = compute_local_rerank_scores(
                    query=query,
                    texts=formatted_passages,
                    backend="sentence_transformers",
                    model_name=rerank_model or DEFAULT_RERANKER_MODEL,
                    rerank_fn=rerank_fn,
                )
                reranked_tuples = []
                for rank_i, (hit_dict, ce_s) in enumerate(zip(target_slice, ce_scores)):
                    prior_s = float(hit_dict.get("score", 0.0))
                    updated_hit = {
                        **hit_dict,
                        "dense_score": prior_s,
                        "rerank_score": round(float(ce_s), 6),
                        "score": round(float(ce_s), 6),
                    }
                    reranked_tuples.append((float(ce_s), prior_s, -rank_i, updated_hit))
                reranked_tuples.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
                scored_hits = [x[3] for x in reranked_tuples] + scored_hits[rerank_pool_n:]

            elapsed_ms = (time.time() - t0) * 1000.0
            logger.debug(f"Search for '{query}' returned {len(scored_hits[:top_limit])} hits in {elapsed_ms:.2f}ms")
            return scored_hits[:top_limit]

        finally:
            conn.close()


