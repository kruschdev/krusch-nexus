"""
KruschNexus Air-Gapped Embeddings Client (embeddings.py)
=======================================================
Direct, zero-cloud embedding client backed by local Ollama.
Features multi-tier text-hash caching (memory + persistent disk/DB cache)
and batching with configurable timeouts.
"""

import os
import re
import json
import sqlite3
import hashlib
import logging
from typing import List, Dict, Optional
import httpx

from .models import NexusConfig

logger = logging.getLogger("krusch_nexus.embeddings")

_MEM_CACHE: Dict[str, List[float]] = {}
MAX_MEM_CACHE_SIZE = 10000


def hash_text(text: str) -> str:
    """Normalize whitespace and compute deterministic SHA-256 hash."""
    clean = re.sub(r'\s+', ' ', text).strip()
    return hashlib.sha256(clean.encode('utf-8')).hexdigest()


def generate_deterministic_vector(text: str, dim: int = 1024) -> List[float]:
    """
    Generate deterministic, unit-normalized float vector for testing and CI.
    Enables full test execution without requiring a live Ollama host.
    """
    import math
    clean = re.sub(r'\s+', ' ', text).strip()
    seed = hashlib.sha256(clean.encode('utf-8')).digest()
    vec = []
    for i in range(dim):
        h = hashlib.sha256(seed + i.to_bytes(4, "big")).digest()
        val = (int.from_bytes(h[:4], "big") / 0xFFFFFFFF) * 2.0 - 1.0
        vec.append(val)
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [round(x / norm, 6) for x in vec]



_RECORDED_VECTORS: Optional[Dict[str, List[float]]] = None


def _get_recorded_vectors() -> Dict[str, List[float]]:
    global _RECORDED_VECTORS
    if _RECORDED_VECTORS is None:
        _RECORDED_VECTORS = {}
        candidate = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "tests", "fixtures", "fixture_embeddings.json")
        if os.path.exists(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    _RECORDED_VECTORS = json.load(f)
            except Exception:
                pass
    return _RECORDED_VECTORS


def _get_disk_cache_conn() -> Optional[sqlite3.Connection]:
    """Get connection to persistent local SQLite embedding cache."""
    try:
        cache_dir = os.path.expanduser("~/.cache/krusch_nexus")
        os.makedirs(cache_dir, exist_ok=True)
        db_path = os.path.join(cache_dir, "embed_cache.sqlite")
        conn = sqlite3.connect(db_path, timeout=5.0)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS disk_embed_cache (
                text_hash TEXT PRIMARY KEY,
                model TEXT NOT NULL,
                vector TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        return conn
    except Exception as e:
        logger.debug(f"Could not open disk cache: {e}")
        return None


def _load_from_persistent_cache(text_hashes: List[str], model: str, db=None) -> Dict[str, List[float]]:
    """Lookup embeddings from database or local disk cache."""
    found: Dict[str, List[float]] = {}
    if not text_hashes:
        return found

    # 1. Try DB session if provided
    if db is not None:
        try:
            from .store import EmbedCache
            records = db.query(EmbedCache).filter(
                EmbedCache.text_hash.in_(text_hashes),
                EmbedCache.model == model
            ).all()
            for r in records:
                try:
                    found[r.text_hash] = json.loads(r.vector)
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"DB embed cache query error: {e}")

    # 2. Try disk SQLite cache for remaining
    remaining = [h for h in text_hashes if h not in found]
    if remaining:
        conn = _get_disk_cache_conn()
        if conn:
            try:
                placeholders = ",".join(["?"] * len(remaining))
                cur = conn.cursor()
                cur.execute(
                    f"SELECT text_hash, vector FROM disk_embed_cache WHERE model = ? AND text_hash IN ({placeholders})",
                    [model] + remaining
                )
                for h, vec_json in cur.fetchall():
                    try:
                        found[h] = json.loads(vec_json)
                    except Exception:
                        pass
                conn.close()
            except Exception as e:
                logger.debug(f"Disk cache read error: {e}")

    return found


def _save_to_persistent_cache(records: List[tuple], model: str, db=None):
    """Save new embeddings to DB and disk cache."""
    if not records:
        return

    # 1. Save to DB session if provided
    if db is not None:
        try:
            from .store import EmbedCache
            for h, vec in records:
                try:
                    existing = db.query(EmbedCache).filter(
                        EmbedCache.text_hash == h,
                        EmbedCache.model == model
                    ).first()
                    if not existing:
                        db.add(EmbedCache(
                            text_hash=h,
                            model=model,
                            dim=len(vec),
                            vector=json.dumps(vec)
                        ))
                except Exception:
                    pass
            db.commit()
        except Exception as e:
            logger.debug(f"DB embed cache write error: {e}")

    # 2. Save to disk SQLite cache
    conn = _get_disk_cache_conn()
    if conn:
        try:
            with conn:
                for h, vec in records:
                    conn.execute(
                        "INSERT OR IGNORE INTO disk_embed_cache (text_hash, model, vector) VALUES (?, ?, ?)",
                        (h, model, json.dumps(vec))
                    )
            conn.close()
        except Exception as e:
            logger.debug(f"Disk cache write error: {e}")


def get_embedding(text: str, config: Optional[NexusConfig] = None, db=None) -> List[float]:
    """Generate 1024-dim vector embedding for a single text chunk via local Ollama."""
    res = get_embeddings_batch([text], config=config, db=db)
    return res[0] if res else []


def get_embeddings_batch(
    texts: List[str],
    config: Optional[NexusConfig] = None,
    db=None
) -> List[List[float]]:
    """
    Generate vector embeddings in batch via local Ollama /api/embed.
    Uses multi-tier hash caching (memory + disk/DB) to skip recomputation for identical chunks.
    """
    if not texts:
        return []

    conf = config or NexusConfig.from_env()
    ollama_url = conf.ollama_url
    model = conf.embed_model
    batch_size = conf.embed_batch_size
    timeout = conf.embed_timeout

    results: List[Optional[List[float]]] = [None] * len(texts)
    missing_indices: List[int] = []
    missing_hashes: List[str] = []
    missing_texts: List[str] = []

    # 1. Check in-memory cache
    for i, t in enumerate(texts):
        h = hash_text(t)
        if h in _MEM_CACHE:
            results[i] = _MEM_CACHE[h]
        else:
            missing_indices.append(i)
            missing_hashes.append(h)
            clean_t = t.strip() if t and t.strip() else " "
            missing_texts.append(clean_t)

    # 2. Check persistent disk/DB cache for items not in memory
    if missing_hashes:
        persisted = _load_from_persistent_cache(missing_hashes, model=model, db=db)
        still_missing_indices: List[int] = []
        still_missing_hashes: List[str] = []
        still_missing_texts: List[str] = []

        for orig_i, h, txt in zip(missing_indices, missing_hashes, missing_texts):
            if h in persisted:
                vec = persisted[h]
                results[orig_i] = vec
                if len(_MEM_CACHE) < MAX_MEM_CACHE_SIZE:
                    _MEM_CACHE[h] = vec
            else:
                still_missing_indices.append(orig_i)
                still_missing_hashes.append(h)
                still_missing_texts.append(txt)

        missing_indices = still_missing_indices
        missing_hashes = still_missing_hashes
        missing_texts = still_missing_texts

    if not missing_texts:
        return [r for r in results if r is not None]

    # 2.5 If dumb/test backend configured, synthesize deterministic vectors for missing texts
    backend = getattr(conf, "embed_backend", None) or os.getenv("NEXUS_EMBED_BACKEND", "").lower()
    if backend in ("dummy", "precomputed", "mock") or getattr(conf, "embedding_provider", "") in ("dummy", "precomputed"):
        dim = conf.embedding_dim or 1024
        rec_vectors = _get_recorded_vectors()
        new_cached_records: List[tuple] = []
        for orig_i, h, txt in zip(missing_indices, missing_hashes, missing_texts):
            if h in rec_vectors:
                vec = rec_vectors[h]
            else:
                vec = generate_deterministic_vector(txt, dim=dim)
            results[orig_i] = vec
            if len(_MEM_CACHE) < MAX_MEM_CACHE_SIZE:
                _MEM_CACHE[h] = vec
            new_cached_records.append((h, vec))
        _save_to_persistent_cache(new_cached_records, model=model, db=db)
        return [r for r in results if r is not None]

    # 3. Request embeddings for truly missing chunks from Ollama
    new_cached_records: List[tuple] = []
    for batch_start in range(0, len(missing_texts), batch_size):
        batch_slice = missing_texts[batch_start:batch_start + batch_size]
        batch_orig_idx = missing_indices[batch_start:batch_start + batch_size]
        batch_h_slice = missing_hashes[batch_start:batch_start + batch_size]

        payload = {
            "model": model,
            "input": batch_slice
        }

        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(f"{ollama_url}/api/embed", json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    embs = data.get("embeddings", [])
                    if len(embs) == len(batch_slice):
                        for orig_i, h, emb in zip(batch_orig_idx, batch_h_slice, embs):
                            if emb and conf.embedding_dim and len(emb) != conf.embedding_dim:
                                from .exceptions import ModelDimensionDriftError
                                raise ModelDimensionDriftError(
                                    f"Ollama returned embedding of dimension {len(emb)}, "
                                    f"but configured embedding_dim is {conf.embedding_dim}."
                                )
                            results[orig_i] = emb
                            new_cached_records.append((h, emb))
                            if len(_MEM_CACHE) < MAX_MEM_CACHE_SIZE:
                                _MEM_CACHE[h] = emb
                        continue

                # Fallback to single /api/embeddings endpoint if /api/embed is not supported
                logger.warning(f"Ollama batch embed returned HTTP {resp.status_code}, falling back to legacy endpoint.")
                for orig_i, h, txt in zip(batch_orig_idx, batch_h_slice, batch_slice):
                    resp_single = client.post(
                        f"{ollama_url}/api/embeddings",
                        json={"model": model, "prompt": txt}
                    )
                    emb = resp_single.json().get("embedding", [])
                    results[orig_i] = emb
                    new_cached_records.append((h, emb))
                    if len(_MEM_CACHE) < MAX_MEM_CACHE_SIZE:
                        _MEM_CACHE[h] = emb
        except Exception as e:
            logger.warning(f"Failed to generate embeddings from {ollama_url}: {e}")
            for orig_i in batch_orig_idx:
                results[orig_i] = []

    # Persist newly computed embeddings to disk / DB
    if new_cached_records:
        _save_to_persistent_cache(new_cached_records, model=model, db=db)

    return [r if r is not None else [] for r in results]
