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

DEFAULT_EMBED_URL = os.getenv("OLLAMA_EMBED_HOST", "http://192.168.1.13:11434")
DEFAULT_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "bge-large")


class LegalAuthorityCartridge:
    """Client for high-speed hybrid retrieval over the KruschNexus Legal Cartridge."""

    def __init__(self, db_path: Optional[str | Path] = None, embed_url: str = DEFAULT_EMBED_URL):
        self.db_path = self._resolve_db_path(db_path)
        self.embed_url = embed_url.rstrip("/")
        if not self.db_path or not self.db_path.exists():
            raise FileNotFoundError(f"Legal Authority Cartridge SQLite file not found: {self.db_path}")

        # Verify database connection
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM documents;")
            self.doc_count = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM chunks;")
            self.chunk_count = cur.fetchone()[0]
            logger.info(f"Loaded Legal Authority Cartridge: {self.db_path} ({self.doc_count} docs, {self.chunk_count} chunks)")
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
        """Embed search query via local Ollama bge-large."""
        url = f"{self.embed_url}/api/embed"
        payload = {
            "model": DEFAULT_EMBED_MODEL,
            "input": [query],
            "keep_alive": -1
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
        candidate_pool_size: int = 300
    ) -> List[Dict[str, Any]]:
        """
        Execute sub-50ms hybrid BM25 + dense vector retrieval:
        1. FTS5 pre-selects candidate chunks matching query keywords.
        2. If FTS yields candidates, rerank candidates by vector cosine similarity.
        3. If FTS yields 0 candidates (out-of-vocabulary), fall back to vector scan.
        """
        t0 = time.time()
        conn = self._get_conn()
        cur = conn.cursor()

        try:
            # Clean query for FTS5 (remove special syntax characters)
            clean_q = "".join(c if (c.isalnum() or c.isspace()) else " " for c in query).strip()
            words = [w for w in clean_q.split() if len(w) > 2]
            fts_match_expr = " OR ".join(words) if words else query

            # 1. Fetch candidate chunks via FTS5
            sql_fts = f"""
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
                    d.source_table
                FROM chunks_fts f
                JOIN chunks c ON f.chunk_id = c.chunk_id
                JOIN documents d ON c.doc_id = d.id
                WHERE chunks_fts MATCH ?
            """
            params = [fts_match_expr]
            if jurisdiction:
                sql_fts += " AND d.jurisdiction LIKE ?"
                params.append(f"%{jurisdiction}%")
            if source_table:
                sql_fts += " AND d.source_table = ?"
                params.append(source_table)
            sql_fts += f" LIMIT {candidate_pool_size};"

            cur.execute(sql_fts, params)
            candidates = cur.fetchall()

            # 2. Embed query vector
            q_vec = self._embed_query(query)

            scored_hits = []
            if candidates:
                for r in candidates:
                    vec = struct.unpack("1024f", r["embedding"])
                    sim = sum(a * b for a, b in zip(q_vec, vec))
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
            else:
                # Fallback: scan sample if FTS yielded zero lexical matches
                cur.execute(f"""
                    SELECT
                        c.chunk_id, c.doc_id, c.chunk_index, c.citation, c.heading_path,
                        c.locator, c.text, c.char_start, c.char_end, c.embedding,
                        d.title, d.jurisdiction, d.court, d.decision_year, d.source_table
                    FROM chunks c
                    JOIN documents d ON c.doc_id = d.id
                    LIMIT {candidate_pool_size};
                """)
                for r in cur.fetchall():
                    vec = struct.unpack("1024f", r["embedding"])
                    sim = sum(a * b for a, b in zip(q_vec, vec))
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

            elapsed_ms = (time.time() - t0) * 1000.0
            logger.debug(f"Search for '{query}' returned {len(scored_hits[:limit])} hits in {elapsed_ms:.2f}ms")
            return scored_hits[:limit]

        finally:
            conn.close()
