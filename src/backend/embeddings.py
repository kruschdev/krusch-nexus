import os
import re
import hashlib
import logging
import httpx
from typing import List, Dict, Optional

logger = logging.getLogger("krusch_nexus.embeddings")

OLLAMA_EMBED_HOST = os.getenv("OLLAMA_EMBED_HOST", os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "bge-large")
EMBED_TIMEOUT = float(os.getenv("EMBED_TIMEOUT", "45.0"))
EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "16"))

# In-memory LRU-style embedding cache by text hash
_EMBED_CACHE: Dict[str, List[float]] = {}
MAX_CACHE_SIZE = 5000


def hash_text(text: str) -> str:
    clean = re.sub(r'\s+', ' ', text).strip()
    return hashlib.sha256(clean.encode('utf-8')).hexdigest()


def get_embedding(text: str) -> List[float]:
    """Generate 1024-dim vector embedding for a single text chunk via local Ollama."""
    res = get_embeddings_batch([text])
    return res[0] if res else []


def get_embeddings_batch(texts: List[str]) -> List[List[float]]:
    """
    Generate vector embeddings in batch via local Ollama /api/embed.
    Uses hash caching to skip recomputation for identical chunks.
    """
    if not texts:
        return []

    results: List[Optional[List[float]]] = [None] * len(texts)
    missing_indices: List[int] = []
    missing_texts: List[str] = []

    for i, t in enumerate(texts):
        h = hash_text(t)
        if h in _EMBED_CACHE:
            results[i] = _EMBED_CACHE[h]
        else:
            missing_indices.append(i)
            # Ollama expects non-empty input
            clean_t = t.strip() if t and t.strip() else " "
            missing_texts.append(clean_t)

    if not missing_texts:
        return [r for r in results if r is not None]

    # Process in batches
    for batch_start in range(0, len(missing_texts), EMBED_BATCH_SIZE):
        batch_slice = missing_texts[batch_start:batch_start + EMBED_BATCH_SIZE]
        batch_orig_idx = missing_indices[batch_start:batch_start + EMBED_BATCH_SIZE]

        payload = {
            "model": OLLAMA_EMBED_MODEL,
            "input": batch_slice
        }

        try:
            with httpx.Client(timeout=EMBED_TIMEOUT) as client:
                resp = client.post(f"{OLLAMA_EMBED_HOST}/api/embed", json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    embs = data.get("embeddings", [])
                    if len(embs) == len(batch_slice):
                        for orig_i, txt, emb in zip(batch_orig_idx, batch_slice, embs):
                            results[orig_i] = emb
                            if len(_EMBED_CACHE) < MAX_CACHE_SIZE:
                                _EMBED_CACHE[hash_text(txt)] = emb
                        continue
                # If /api/embed failed, attempt legacy /api/embeddings sequentially
                logger.warning(f"Ollama batch embed returned HTTP {resp.status_code}, falling back to legacy endpoint.")
                for orig_i, txt in zip(batch_orig_idx, batch_slice):
                    resp_single = client.post(
                        f"{OLLAMA_EMBED_HOST}/api/embeddings",
                        json={"model": OLLAMA_EMBED_MODEL, "prompt": txt}
                    )
                    emb = resp_single.json().get("embedding", [])
                    results[orig_i] = emb
                    if len(_EMBED_CACHE) < MAX_CACHE_SIZE:
                        _EMBED_CACHE[hash_text(txt)] = emb
        except Exception as e:
            logger.error(f"Failed to generate embeddings from {OLLAMA_EMBED_HOST}: {e}")
            # Fill with empty or zero vectors on fatal connection failure
            for orig_i in batch_orig_idx:
                results[orig_i] = []

    return [r if r is not None else [] for r in results]
