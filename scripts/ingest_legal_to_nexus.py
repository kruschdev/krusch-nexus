#!/usr/bin/env python3
"""
KruschNexus Legal Authority Ingestion & Cartridge Forge (ingest_legal_to_nexus.py)
==================================================================================
Streams case law and municipal codes from PostgreSQL (pocketlawyer-db) into
a high-performance, standalone KruschNexus Legal Authority Cartridge (SQLite)
with 1,024-dimensional bge-large vector embeddings and KruschNexus citation-first
structural orientation.

Orientation Features:
- Clean raw text chunking via KruschNexus chunking engine (no breadcrumb pollution)
- Strict source_hash computed over raw text only
- Canonical citation strings and hierarchical heading paths
- Spatial char_start / char_end offsets
- DocType.AUTHORITY classification
- SQLite storage with IEEE 754 float32 vector blobs (4,096 bytes / chunk)
- FTS5 full-text search virtual table for hybrid BM25 + Vector queries
- Idempotent and resumable via ingest_ledger
- Automatic sync to /mnt/media1 on kruschserv
"""

import os
import sys
import time
import json
import struct
import signal
import hashlib
import logging
import sqlite3
import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Set

import httpx
import psycopg2
from psycopg2.extras import RealDictCursor

# Ensure krusch_nexus package is importable
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from krusch_nexus.chunking import chunk_document_pages, compute_chunk_hash, format_chunk_for_embedding
from krusch_nexus.models import PageData, DocType

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("nexus_legal_ingest")

# Database connection defaults
DEFAULT_PG_HOST = os.getenv("POCKETLAWYER_DB_HOST", "127.0.0.1")
DEFAULT_PG_PORT = int(os.getenv("POCKETLAWYER_DB_PORT", "5433"))
DEFAULT_PG_USER = os.getenv("POCKETLAWYER_DB_USER", "pocketlawyer")
DEFAULT_PG_PASS = os.getenv("POCKETLAWYER_DB_PASS", "")
DEFAULT_PG_NAME = os.getenv("POCKETLAWYER_DB_NAME", "pocketlawyer")

DEFAULT_OLLAMA_URL = os.getenv("OLLAMA_EMBED_HOST", "http://127.0.0.1:11434")
DEFAULT_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "bge-large")
DEFAULT_EMBED_DIM = 1024

# Graceful exit handler
INTERRUPT_REQUESTED = False


def sigint_handler(sig, frame):
    global INTERRUPT_REQUESTED
    logger.warning("Interrupt signal received. Gracefully concluding current batch...")
    INTERRUPT_REQUESTED = True


signal.signal(signal.SIGINT, sigint_handler)
signal.signal(signal.SIGTERM, sigint_handler)


def init_cartridge_db(db_path: Path) -> sqlite3.Connection:
    """Initialize the SQLite cartridge schema with FTS5 and vector blob columns."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA cache_size = -64000;")  # 64MB cache
    conn.execute("PRAGMA foreign_keys = ON;")

    with conn:
        # 1. Manifest
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cartridge_manifest (
                pack_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                version TEXT NOT NULL,
                sku TEXT NOT NULL,
                publisher TEXT NOT NULL,
                embed_model TEXT NOT NULL,
                embed_dim INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                description TEXT,
                metadata JSON
            )
        """)

        # 2. Documents
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                source_table TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                title TEXT NOT NULL,
                citation TEXT,
                court TEXT,
                jurisdiction TEXT,
                decision_date TEXT,
                decision_year INTEGER,
                doc_type TEXT DEFAULT 'authority',
                source_hash TEXT NOT NULL,
                total_chunks INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata JSON
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_docs_source ON documents(source_table, source_record_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_docs_jurisdiction ON documents(jurisdiction);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_docs_citation ON documents(citation);")

        # 3. Chunks with IEEE 754 float32 blob embeddings
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                citation TEXT NOT NULL,
                heading_path TEXT,
                locator TEXT,
                text TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                char_start INTEGER,
                char_end INTEGER,
                confidence REAL DEFAULT 1.0,
                embedding BLOB NOT NULL,
                FOREIGN KEY(doc_id) REFERENCES documents(id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_source_hash ON chunks(source_hash);")

        # 4. FTS5 Virtual Table for Lexical/Hybrid BM25 queries
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                chunk_id UNINDEXED,
                doc_id UNINDEXED,
                citation,
                heading_path,
                text,
                tokenize='porter unicode61'
            )
        """)

        # 5. Ingestion Ledger for Idempotency
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ingest_ledger (
                doc_id TEXT PRIMARY KEY,
                source_table TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                status TEXT NOT NULL,
                chunks_count INTEGER NOT NULL,
                duration_ms REAL DEFAULT 0.0,
                error_message TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_status ON ingest_ledger(source_table, status);")

        # Record manifest entry if empty
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM cartridge_manifest WHERE pack_id = 'krusch-nexus-legal-authority-v1';")
        if cur.fetchone()[0] == 0:
            conn.execute("""
                INSERT INTO cartridge_manifest (
                    pack_id, title, version, sku, publisher, embed_model, embed_dim, description, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "krusch-nexus-legal-authority-v1",
                "KruschNexus Sovereign Legal Authority Cartridge",
                "1.0.0",
                "jurisdiction",
                "kruschdev",
                DEFAULT_EMBED_MODEL,
                DEFAULT_EMBED_DIM,
                "Case law opinions and municipal code ordinances chunked via KruschNexus with bge-large embeddings",
                json.dumps({"source": "pocketlawyer-db", "dim": DEFAULT_EMBED_DIM, "architecture": "KruschNexus-v0.2.5"})
            ))

    return conn


def get_completed_record_ids(conn: sqlite3.Connection, source_table: str) -> Set[str]:
    """Retrieve set of already completed source record IDs for resumption."""
    cur = conn.cursor()
    cur.execute(
        "SELECT source_record_id FROM ingest_ledger WHERE source_table = ? AND status = 'completed';",
        (source_table,)
    )
    return {str(row[0]) for row in cur.fetchall()}


import concurrent.futures

class EmbedClientPool:
    """Concurrent parallel load balancer across multiple Ollama embedding instances."""
    def __init__(
        self,
        endpoints: List[str],
        model: str = DEFAULT_EMBED_MODEL,
        timeout: float = 60.0
    ):
        self.endpoints = [ep.rstrip("/") for ep in endpoints if ep.strip()]
        if not self.endpoints:
            self.endpoints = [DEFAULT_OLLAMA_URL.rstrip("/")]
        self.model = model
        self.timeout = timeout
        self.current_idx = 0
        logger.info(f"Initialized EmbedClientPool with {len(self.endpoints)} endpoints: {self.endpoints}")

    def _embed_single(self, endpoint: str, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        url = f"{endpoint}/api/embed"
        payload = {
            "model": self.model,
            "input": texts,
            "keep_alive": -1
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                embeddings = data.get("embeddings")
                if embeddings and len(embeddings) == len(texts):
                    return embeddings
                raise ValueError(f"{endpoint} returned {len(embeddings) if embeddings else 0} vectors for {len(texts)} texts")
        except Exception as e:
            logger.warning(f"Error on {endpoint} ({e}), trying fallback...")
            for fallback in self.endpoints:
                if fallback == endpoint:
                    continue
                try:
                    f_url = f"{fallback}/api/embed"
                    with httpx.Client(timeout=self.timeout) as client:
                        resp = client.post(f_url, json=payload)
                        resp.raise_for_status()
                        embs = resp.json().get("embeddings")
                        if embs and len(embs) == len(texts):
                            return embs
                except Exception:
                    continue
            raise RuntimeError(f"All endpoints failed for batch: {e}")

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        k = len(self.endpoints)
        if k == 1 or len(texts) < 16:
            return self._embed_single(self.endpoints[0], texts)

        # Split batch evenly across all endpoints for simultaneous GPU execution
        results = [None] * len(texts)
        with concurrent.futures.ThreadPoolExecutor(max_workers=k) as executor:
            future_to_indices = {}
            for ep_idx, ep in enumerate(self.endpoints):
                indices = list(range(ep_idx, len(texts), k))
                if indices:
                    sub_texts = [texts[idx] for idx in indices]
                    fut = executor.submit(self._embed_single, ep, sub_texts)
                    future_to_indices[fut] = indices

            for fut in concurrent.futures.as_completed(future_to_indices):
                indices = future_to_indices[fut]
                sub_vecs = fut.result()
                for idx, vec in zip(indices, sub_vecs):
                    results[idx] = vec

        return results


def pack_vector(vector: List[float]) -> bytes:
    """Pack float vector into IEEE 754 32-bit float bytes."""
    return struct.pack(f"{len(vector)}f", *vector)


def unpack_vector(blob: bytes, dim: int = 1024) -> Tuple[float, ...]:
    """Unpack float32 byte blob back into float tuple."""
    return struct.unpack(f"{dim}f", blob)


def process_cases_stream(
    pg_conn,
    sqlite_conn: sqlite3.Connection,
    completed_ids: Set[str],
    embed_pool: EmbedClientPool,
    limit: Optional[int] = None,
    batch_size: int = 32,
    db_commit_interval: int = 50,
    fleet_sync_interval: int = 1000,
    cartridge_path: Optional[Path] = None,
    dry_run: bool = False
) -> Dict[str, Any]:
    """Extract, chunk, embed, and store case opinions."""
    logger.info("Opening server-side cursor for substantive cases (length(opinion_text) > 50)...")
    pg_cursor = pg_conn.cursor(name="cases_stream_cursor", cursor_factory=RealDictCursor)
    query = """
        SELECT
            id,
            cluster_id,
            case_name,
            case_name_short,
            citation,
            court,
            jurisdiction,
            decision_date,
            decision_year,
            docket_number,
            judges,
            nature_of_suit,
            precedential_status,
            state,
            court_short_name,
            opinion_text
        FROM cases
        WHERE length(opinion_text) > 50
        ORDER BY id ASC
    """
    if limit:
        query += f" LIMIT {limit}"

    pg_cursor.execute(query)

    docs_buffer = []
    chunks_buffer = []
    fts_buffer = []
    ledger_buffer = []

    pending_embed_texts = []
    pending_chunk_records = []

    total_cases = 0
    total_chunks = 0
    skipped_count = 0
    last_sync_cases = 0
    t_start = time.time()

    def flush_db_batch():
        nonlocal docs_buffer, chunks_buffer, fts_buffer, ledger_buffer
        if not docs_buffer:
            return
        with sqlite_conn:
            sqlite_conn.executemany("""
                INSERT OR REPLACE INTO documents (
                    id, source_table, source_record_id, title, citation, court, jurisdiction,
                    decision_date, decision_year, doc_type, source_hash, total_chunks, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, docs_buffer)

            sqlite_conn.executemany("""
                INSERT OR REPLACE INTO chunks (
                    chunk_id, doc_id, chunk_index, citation, heading_path, locator, text,
                    source_hash, char_start, char_end, confidence, embedding
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, chunks_buffer)

            sqlite_conn.executemany("""
                INSERT OR REPLACE INTO chunks_fts (
                    chunk_id, doc_id, citation, heading_path, text
                ) VALUES (?, ?, ?, ?, ?)
            """, fts_buffer)

            sqlite_conn.executemany("""
                INSERT OR REPLACE INTO ingest_ledger (
                    doc_id, source_table, source_record_id, status, chunks_count, duration_ms, processed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ledger_buffer)

        docs_buffer.clear()
        chunks_buffer.clear()
        fts_buffer.clear()
        ledger_buffer.clear()

    def flush_embed_batch():
        nonlocal pending_embed_texts, pending_chunk_records, chunks_buffer, fts_buffer, total_chunks
        if not pending_embed_texts:
            return

        if not dry_run:
            vectors = embed_pool.embed_batch(pending_embed_texts)
        else:
            vectors = [[0.0] * DEFAULT_EMBED_DIM for _ in pending_embed_texts]

        for (c_meta, fts_meta), vec in zip(pending_chunk_records, vectors):
            packed = pack_vector(vec)
            chunks_buffer.append((
                c_meta["chunk_id"],
                c_meta["doc_id"],
                c_meta["chunk_index"],
                c_meta["citation"],
                c_meta["heading_path"],
                c_meta["locator"],
                c_meta["text"],
                c_meta["source_hash"],
                c_meta["char_start"],
                c_meta["char_end"],
                c_meta["confidence"],
                packed
            ))
            fts_buffer.append((
                fts_meta["chunk_id"],
                fts_meta["doc_id"],
                fts_meta["citation"],
                fts_meta["heading_path"],
                fts_meta["text"]
            ))

        total_chunks += len(pending_embed_texts)
        pending_embed_texts.clear()
        pending_chunk_records.clear()

    try:
        while True:
            if INTERRUPT_REQUESTED:
                break

            rows = pg_cursor.fetchmany(100)
            if not rows:
                break

            for row in rows:
                if INTERRUPT_REQUESTED:
                    break

                rec_id = str(row["id"])
                if rec_id in completed_ids:
                    skipped_count += 1
                    continue

                case_start = time.time()
                doc_id = f"case_{rec_id}"
                case_name = (row["case_name"] or row["case_name_short"] or f"Case {rec_id}").strip()
                citation = (row["citation"] or "").strip()
                court = (row["court"] or row["court_short_name"] or "").strip()
                jurisdiction = (row["jurisdiction"] or row["state"] or "US").strip()
                decision_date = str(row["decision_date"]) if row["decision_date"] else None
                decision_year = int(row["decision_year"]) if row["decision_year"] else None
                text_content = row["opinion_text"] or ""

                doc_hash = hashlib.sha256(text_content.encode("utf-8")).hexdigest()

                # KruschNexus Citation-First Chunking
                canonical_doc_citation = citation if citation else f"{case_name} ({decision_year or 'N/A'})"
                page = PageData(
                    index=1,
                    text=text_content,
                    locator=case_name
                )
                raw_chunks = chunk_document_pages(
                    pages=[page],
                    filename=f"{case_name[:40]}_{rec_id}.txt",
                    file_hash=doc_hash,
                    max_chars=1800,
                    overlap_chars=150,
                    doc_type=DocType.AUTHORITY,
                    base_metadata={
                        "case_name": case_name,
                        "citation": citation,
                        "court": court,
                        "decision_year": decision_year
                    }
                )

                chunk_count = len(raw_chunks)
                for idx, c in enumerate(raw_chunks):
                    chunk_id = f"{doc_id}_c{idx}"
                    chunk_citation = f"{canonical_doc_citation} § Opinion [Chunk {idx+1}]"
                    heading_hierarchy = [h for h in [court or jurisdiction, case_name, "Opinion"] if h]

                    c_meta = {
                        "chunk_id": chunk_id,
                        "doc_id": doc_id,
                        "chunk_index": idx,
                        "citation": chunk_citation,
                        "heading_path": json.dumps(heading_hierarchy),
                        "locator": f"Page 1 > Chunk {idx+1}",
                        "text": c.text,
                        "source_hash": c.source_hash,
                        "char_start": c.char_start,
                        "char_end": c.char_end,
                        "confidence": 1.0
                    }
                    fts_meta = {
                        "chunk_id": chunk_id,
                        "doc_id": doc_id,
                        "citation": chunk_citation,
                        "heading_path": " > ".join(heading_hierarchy),
                        "text": c.text
                    }

                    pending_embed_texts.append(
                        format_chunk_for_embedding(
                            c,
                            doc_title=canonical_doc_citation,
                            heading_override=heading_hierarchy
                        )
                    )
                    pending_chunk_records.append((c_meta, fts_meta))

                    if len(pending_embed_texts) >= batch_size:
                        flush_embed_batch()

                metadata_payload = {
                    "cluster_id": row["cluster_id"],
                    "case_name_short": row["case_name_short"],
                    "docket_number": row["docket_number"],
                    "judges": row["judges"],
                    "nature_of_suit": row["nature_of_suit"],
                    "precedential_status": row["precedential_status"]
                }

                docs_buffer.append((
                    doc_id,
                    "cases",
                    rec_id,
                    case_name,
                    citation,
                    court,
                    jurisdiction,
                    decision_date,
                    decision_year,
                    "authority",
                    doc_hash,
                    chunk_count,
                    json.dumps(metadata_payload)
                ))

                duration_ms = (time.time() - case_start) * 1000.0
                ledger_buffer.append((
                    doc_id,
                    "cases",
                    rec_id,
                    "completed",
                    chunk_count,
                    duration_ms,
                    datetime.now(timezone.utc).isoformat()
                ))

                completed_ids.add(rec_id)
                total_cases += 1

                if len(docs_buffer) >= db_commit_interval:
                    flush_embed_batch()
                    flush_db_batch()
                    elapsed = time.time() - t_start
                    cases_rate = total_cases / elapsed if elapsed > 0 else 0
                    chunks_rate = total_chunks / elapsed if elapsed > 0 else 0
                    logger.info(
                        f"Processed {total_cases} cases ({total_chunks} chunks embedded) | "
                        f"{cases_rate:.1f} cases/s, {chunks_rate:.1f} chunks/s | Skipped {skipped_count}"
                    )
                    if cartridge_path and (total_cases - last_sync_cases) >= fleet_sync_interval:
                        last_sync_cases = total_cases
                        sync_cartridge_to_fleet(cartridge_path)

        # Final flush
        flush_embed_batch()
        flush_db_batch()
        if cartridge_path:
            sync_cartridge_to_fleet(cartridge_path)

    finally:
        pg_cursor.close()

    elapsed = time.time() - t_start
    return {
        "source": "cases",
        "processed_cases": total_cases,
        "processed_chunks": total_chunks,
        "skipped_cases": skipped_count,
        "elapsed_seconds": round(elapsed, 2)
    }


def process_local_codes_stream(
    pg_conn,
    sqlite_conn: sqlite3.Connection,
    completed_ids: Set[str],
    embed_pool: EmbedClientPool,
    data_type: str = "municipal_code",
    limit: Optional[int] = None,
    batch_size: int = 32,
    db_commit_interval: int = 50,
    dry_run: bool = False
) -> Dict[str, Any]:
    """Extract, chunk, embed, and store municipal codes or eviction notices."""
    logger.info(f"Opening server-side cursor for local_codes (data_type='{data_type}')...")
    pg_cursor = pg_conn.cursor(name="local_codes_cursor", cursor_factory=RealDictCursor)
    query = """
        SELECT
            id,
            jurisdiction,
            data_type,
            record_id,
            content,
            source,
            source_type,
            date_imported
        FROM local_codes
        WHERE data_type = %s AND length(content) > 30
        ORDER BY id ASC
    """
    if limit:
        query += f" LIMIT {limit}"

    pg_cursor.execute(query, (data_type,))

    docs_buffer = []
    chunks_buffer = []
    fts_buffer = []
    ledger_buffer = []

    pending_embed_texts = []
    pending_chunk_records = []

    total_records = 0
    total_chunks = 0
    skipped_count = 0
    t_start = time.time()

    def flush_db_batch():
        nonlocal docs_buffer, chunks_buffer, fts_buffer, ledger_buffer
        if not docs_buffer:
            return
        with sqlite_conn:
            sqlite_conn.executemany("""
                INSERT OR REPLACE INTO documents (
                    id, source_table, source_record_id, title, citation, court, jurisdiction,
                    decision_date, decision_year, doc_type, source_hash, total_chunks, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, docs_buffer)

            sqlite_conn.executemany("""
                INSERT OR REPLACE INTO chunks (
                    chunk_id, doc_id, chunk_index, citation, heading_path, locator, text,
                    source_hash, char_start, char_end, confidence, embedding
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, chunks_buffer)

            sqlite_conn.executemany("""
                INSERT OR REPLACE INTO chunks_fts (
                    chunk_id, doc_id, citation, heading_path, text
                ) VALUES (?, ?, ?, ?, ?)
            """, fts_buffer)

            sqlite_conn.executemany("""
                INSERT OR REPLACE INTO ingest_ledger (
                    doc_id, source_table, source_record_id, status, chunks_count, duration_ms, processed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ledger_buffer)

        docs_buffer.clear()
        chunks_buffer.clear()
        fts_buffer.clear()
        ledger_buffer.clear()

    def flush_embed_batch():
        nonlocal pending_embed_texts, pending_chunk_records, chunks_buffer, fts_buffer, total_chunks
        if not pending_embed_texts:
            return

        if not dry_run:
            vectors = embed_pool.embed_batch(pending_embed_texts)
        else:
            vectors = [[0.0] * DEFAULT_EMBED_DIM for _ in pending_embed_texts]

        for (c_meta, fts_meta), vec in zip(pending_chunk_records, vectors):
            packed = pack_vector(vec)
            chunks_buffer.append((
                c_meta["chunk_id"],
                c_meta["doc_id"],
                c_meta["chunk_index"],
                c_meta["citation"],
                c_meta["heading_path"],
                c_meta["locator"],
                c_meta["text"],
                c_meta["source_hash"],
                c_meta["char_start"],
                c_meta["char_end"],
                c_meta["confidence"],
                packed
            ))
            fts_buffer.append((
                fts_meta["chunk_id"],
                fts_meta["doc_id"],
                fts_meta["citation"],
                fts_meta["heading_path"],
                fts_meta["text"]
            ))

        total_chunks += len(pending_embed_texts)
        pending_embed_texts.clear()
        pending_chunk_records.clear()

    try:
        while True:
            if INTERRUPT_REQUESTED:
                break

            rows = pg_cursor.fetchmany(100)
            if not rows:
                break

            for row in rows:
                if INTERRUPT_REQUESTED:
                    break

                rec_id = str(row["id"])
                ledger_key = f"{data_type}_{rec_id}"
                if rec_id in completed_ids or ledger_key in completed_ids:
                    skipped_count += 1
                    continue

                rec_start = time.time()
                doc_id = f"lc_{data_type}_{rec_id}"
                jurisdiction = (row["jurisdiction"] or "california").replace("_", " ").title()
                content = row["content"] or ""
                doc_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

                # Extract title from first line
                first_line = content.strip().split("\n")[0][:100]
                title = f"{jurisdiction} - {first_line}"
                canonical_citation = f"{jurisdiction} Municipal Code § {row['record_id'] or rec_id}"

                page = PageData(
                    index=1,
                    text=content,
                    locator=title
                )
                raw_chunks = chunk_document_pages(
                    pages=[page],
                    filename=f"{data_type}_{jurisdiction}_{rec_id}.txt",
                    file_hash=doc_hash,
                    max_chars=1800,
                    overlap_chars=150,
                    doc_type=DocType.AUTHORITY,
                    base_metadata={
                        "jurisdiction": jurisdiction,
                        "data_type": data_type,
                        "record_id": row["record_id"]
                    }
                )

                chunk_count = len(raw_chunks)
                for idx, c in enumerate(raw_chunks):
                    chunk_id = f"{doc_id}_c{idx}"
                    chunk_citation = f"{canonical_citation} [Chunk {idx+1}]"
                    heading_hierarchy = [jurisdiction, "Municipal Code", first_line[:50]]

                    c_meta = {
                        "chunk_id": chunk_id,
                        "doc_id": doc_id,
                        "chunk_index": idx,
                        "citation": chunk_citation,
                        "heading_path": json.dumps(heading_hierarchy),
                        "locator": f"Page 1 > Chunk {idx+1}",
                        "text": c.text,
                        "source_hash": c.source_hash,
                        "char_start": c.char_start,
                        "char_end": c.char_end,
                        "confidence": 1.0
                    }
                    fts_meta = {
                        "chunk_id": chunk_id,
                        "doc_id": doc_id,
                        "citation": chunk_citation,
                        "heading_path": " > ".join(heading_hierarchy),
                        "text": c.text
                    }

                    pending_embed_texts.append(
                        format_chunk_for_embedding(
                            c,
                            doc_title=title,
                            heading_override=heading_hierarchy
                        )
                    )
                    pending_chunk_records.append((c_meta, fts_meta))

                    if len(pending_embed_texts) >= batch_size:
                        flush_embed_batch()

                metadata_payload = {
                    "data_type": data_type,
                    "record_id": row["record_id"],
                    "source": row["source"],
                    "source_type": row["source_type"],
                    "date_imported": str(row["date_imported"])
                }

                docs_buffer.append((
                    doc_id,
                    "local_codes",
                    rec_id,
                    title,
                    canonical_citation,
                    None,
                    jurisdiction,
                    None,
                    None,
                    "authority",
                    doc_hash,
                    chunk_count,
                    json.dumps(metadata_payload)
                ))

                duration_ms = (time.time() - rec_start) * 1000.0
                ledger_buffer.append((
                    doc_id,
                    "local_codes",
                    rec_id,
                    "completed",
                    chunk_count,
                    duration_ms,
                    datetime.now(timezone.utc).isoformat()
                ))

                completed_ids.add(rec_id)
                total_records += 1

                if len(docs_buffer) >= db_commit_interval:
                    flush_embed_batch()
                    flush_db_batch()
                    elapsed = time.time() - t_start
                    rec_rate = total_records / elapsed if elapsed > 0 else 0
                    chunks_rate = total_chunks / elapsed if elapsed > 0 else 0
                    logger.info(
                        f"Processed {total_records} {data_type} records ({total_chunks} chunks) | "
                        f"{rec_rate:.1f} rec/s, {chunks_rate:.1f} chunks/s | Skipped {skipped_count}"
                    )

        # Final flush
        flush_embed_batch()
        flush_db_batch()

    finally:
        pg_cursor.close()

    elapsed = time.time() - t_start
    return {
        "source": f"local_codes_{data_type}",
        "processed_records": total_records,
        "processed_chunks": total_chunks,
        "skipped_records": skipped_count,
        "elapsed_seconds": round(elapsed, 2)
    }


def sync_cartridge_to_fleet(local_db_path: Path, remote_dest: str = "kruschserv:/mnt/media1/krusch-nexus/cartridges/"):
    """Sync the local SQLite cartridge database and WAL to kruschserv /mnt/media1."""
    logger.info(f"Syncing cartridge '{local_db_path.name}' to remote fleet target '{remote_dest}'...")
    try:
        cmd = [
            "rsync", "-avz", "--progress",
            str(local_db_path),
            remote_dest
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            logger.info("Fleet sync complete.")
        else:
            logger.warning(f"Rsync completed with warning/error (code {res.returncode}): {res.stderr}")
    except Exception as e:
        logger.error(f"Fleet sync execution error: {e}")


def export_nexus_workspace_bundle(sqlite_path: Path, output_tar: Path):
    """Package the SQLite cartridge into a standard KruschNexus workspace .tar.gz bundle."""
    import tarfile
    import tempfile

    logger.info(f"Exporting KruschNexus workspace bundle from '{sqlite_path}' to '{output_tar}'...")
    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row

    with tempfile.TemporaryDirectory() as tmp_dir:
        staging = Path(tmp_dir)
        reports_dir = staging / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)

        # 1. workspace.json
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM documents;")
        doc_count = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM chunks;")
        chunk_count = cur.fetchone()[0]

        ws_meta = {
            "schema_version": "1.0",
            "name": "LegalAuthority",
            "description": "KruschNexus Sovereign Legal Authority Cartridge (Case Law & Municipal Codes)",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "documents_count": doc_count,
            "chunks_count": chunk_count,
            "embedding_model": DEFAULT_EMBED_MODEL,
            "embedding_dim": DEFAULT_EMBED_DIM
        }
        with open(staging / "workspace.json", "w", encoding="utf-8") as f:
            json.dump(ws_meta, f, indent=2)

        # 2. manifest.json
        cur.execute("SELECT * FROM documents;")
        manifest_items = []
        for r in cur.fetchall():
            manifest_items.append({
                "id": r["id"],
                "filename": f"{r['id']}.txt",
                "file_hash": r["source_hash"],
                "doc_type": r["doc_type"],
                "title": r["title"],
                "citation": r["citation"],
                "court": r["court"],
                "jurisdiction": r["jurisdiction"],
                "decision_year": r["decision_year"],
                "total_chunks": r["total_chunks"]
            })
        with open(staging / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest_items, f, indent=2)

        # 3. chunks.jsonl
        cur.execute("SELECT * FROM chunks ORDER BY doc_id, chunk_index;")
        with open(staging / "chunks.jsonl", "w", encoding="utf-8") as cf:
            for r in cur.fetchall():
                c_dict = {
                    "chunk_id": r["chunk_id"],
                    "document_id": r["doc_id"],
                    "chunk_index": r["chunk_index"],
                    "citation": r["citation"],
                    "heading_path": json.loads(r["heading_path"]) if r["heading_path"] else [],
                    "locator": r["locator"],
                    "content": r["text"],
                    "source_hash": r["source_hash"],
                    "char_start": r["char_start"],
                    "char_end": r["char_end"]
                }
                cf.write(json.dumps(c_dict) + "\n")

        # 4. Include the SQLite cartridge file itself
        import shutil
        shutil.copy2(str(sqlite_path), str(staging / "cartridge.sqlite"))

        output_tar.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(str(output_tar), "w:gz") as tar:
            for item in staging.iterdir():
                tar.add(str(item), arcname=item.name)

    logger.info(f"Workspace bundle saved to '{output_tar}' ({doc_count} docs, {chunk_count} chunks)")


def main():
    parser = argparse.ArgumentParser(description="KruschNexus Legal Authority Ingestion & Cartridge Forge")
    parser.add_argument("--source", choices=["cases", "municipal_code", "eviction_notices", "all"], default="cases",
                        help="Data source to ingest from pocketlawyer-db")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit number of documents to ingest (useful for pilot/verification)")
    parser.add_argument("--batch-size", type=int, default=32,
                        help="Ollama embedding batch size")
    parser.add_argument("--commit-interval", type=int, default=50,
                        help="Database commit interval in documents")
    parser.add_argument("--output-db", type=str, default=None,
                        help="Path to output SQLite database")
    parser.add_argument("--embed-endpoints", type=str, default=DEFAULT_OLLAMA_URL,
                        help="Comma-separated Ollama embedding URLs for round-robin pooling")
    parser.add_argument("--embed-model", type=str, default=DEFAULT_EMBED_MODEL,
                        help="Embedding model name (default: bge-large)")
    parser.add_argument("--fleet-sync-interval", type=int, default=1000,
                        help="Rsync to kruschserv /mnt/media1 after every N documents")
    parser.add_argument("--sync-fleet", action="store_true",
                        help="Sync cartridge to kruschserv /mnt/media1 after run")
    parser.add_argument("--export-workspace", action="store_true",
                        help="Export complete KruschNexus .tar.gz workspace archive")
    parser.add_argument("--dry-run", action="store_true",
                        help="Chunk without generating vectors")
    parser.add_argument("--stats", action="store_true",
                        help="Display statistics for existing cartridge")
    args = parser.parse_args()

    # Determine cartridge path: default to /mnt/media1 if mounted, otherwise local repo data/cartridges/
    if args.output_db:
        cartridge_path = Path(args.output_db).resolve()
    else:
        media1_candidate = Path("/mnt/media1/krusch-nexus/cartridges/cases_nexus_authority.sqlite")
        if media1_candidate.parent.exists() and os.access(media1_candidate.parent, os.W_OK):
            cartridge_path = media1_candidate
        else:
            cartridge_path = REPO_ROOT / "data" / "cartridges" / "cases_nexus_authority.sqlite"

    logger.info(f"Target KruschNexus Cartridge: {cartridge_path}")
    sqlite_conn = init_cartridge_db(cartridge_path)

    if args.stats:
        cur = sqlite_conn.cursor()
        cur.execute("SELECT count(*) FROM documents;")
        d_cnt = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM chunks;")
        c_cnt = cur.fetchone()[0]
        cur.execute("SELECT status, count(*) FROM ingest_ledger GROUP BY status;")
        ledger_stats = cur.fetchall()
        size_mb = cartridge_path.stat().st_size / (1024 * 1024) if cartridge_path.exists() else 0.0

        print("\n=== KruschNexus Legal Cartridge Stats ===")
        print(f"Path: {cartridge_path}")
        print(f"Size: {size_mb:.2f} MB")
        print(f"Total Documents: {d_cnt}")
        print(f"Total Chunks: {c_cnt}")
        print("Ingest Ledger:")
        for st, count in ledger_stats:
            print(f"  - {st}: {count}")
        print("=========================================\n")
        return

    # Initialize EmbedClientPool
    endpoints = [ep.strip() for ep in args.embed_endpoints.split(",") if ep.strip()]
    embed_pool = EmbedClientPool(endpoints=endpoints, model=args.embed_model)

    # Connect to PostgreSQL pocketlawyer-db
    logger.info(f"Connecting to source PostgreSQL pocketlawyer-db at {DEFAULT_PG_HOST}:{DEFAULT_PG_PORT}...")
    try:
        pg_conn = psycopg2.connect(
            host=DEFAULT_PG_HOST,
            port=DEFAULT_PG_PORT,
            user=DEFAULT_PG_USER,
            password=DEFAULT_PG_PASS,
            dbname=DEFAULT_PG_NAME
        )
    except Exception as e:
        logger.error(f"Failed to connect to pocketlawyer-db: {e}")
        sys.exit(1)

    completed_ids = get_completed_record_ids(sqlite_conn, "cases" if args.source == "cases" else "local_codes")
    logger.info(f"Resuming with {len(completed_ids)} already completed documents in ledger.")

    results = []

    try:
        if args.source in ("cases", "all"):
            res = process_cases_stream(
                pg_conn=pg_conn,
                sqlite_conn=sqlite_conn,
                completed_ids=completed_ids,
                embed_pool=embed_pool,
                limit=args.limit,
                batch_size=args.batch_size,
                db_commit_interval=args.commit_interval,
                fleet_sync_interval=args.fleet_sync_interval,
                cartridge_path=cartridge_path,
                dry_run=args.dry_run
            )
            results.append(res)

        if args.source in ("municipal_code", "all"):
            mc_completed = get_completed_record_ids(sqlite_conn, "local_codes")
            res = process_local_codes_stream(
                pg_conn=pg_conn,
                sqlite_conn=sqlite_conn,
                completed_ids=mc_completed,
                embed_pool=embed_pool,
                data_type="municipal_code",
                limit=args.limit,
                batch_size=args.batch_size,
                db_commit_interval=args.commit_interval,
                dry_run=args.dry_run
            )
            results.append(res)

        if args.source in ("eviction_notices", "all"):
            ev_completed = get_completed_record_ids(sqlite_conn, "local_codes")
            res = process_local_codes_stream(
                pg_conn=pg_conn,
                sqlite_conn=sqlite_conn,
                completed_ids=ev_completed,
                embed_pool=embed_pool,
                data_type="eviction_notices",
                limit=args.limit,
                batch_size=args.batch_size,
                db_commit_interval=args.commit_interval,
                dry_run=args.dry_run
            )
            results.append(res)

    finally:
        pg_conn.close()
        sqlite_conn.close()

    logger.info(f"Ingestion batch finished: {results}")

    if args.sync_fleet:
        sync_cartridge_to_fleet(cartridge_path)

    if args.export_workspace:
        bundle_tar = cartridge_path.parent / "cases_nexus_workspace.tar.gz"
        export_nexus_workspace_bundle(cartridge_path, bundle_tar)


if __name__ == "__main__":
    main()
