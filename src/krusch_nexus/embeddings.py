"""
KruschNexus Air-Gapped Embeddings Client
=======================================
Direct, zero-cloud embedding client backed by local Ollama.
Features text-hash caching and batching with configurable timeouts.
"""

import os
import re
import hashlib
import logging
import httpx
from typing import List, Dict, Optional

from .config import NexusConfig
from .exceptions import EmbeddingUnavailable

logger = logging.getLogger("krusch_nexus.embeddings")

_EMBED_CACHE: Dict[str, List[float]] = {}
MAX_CACHE_SIZE = 5000


def hash_text(text: str) -> str:
    clean = re.sub(r'\s+', ' ', text).strip()
    return hashlib.sha256(clean.encode('utf-8')).hexdigest()


def get_embedding(text: str, config: Optional[NexusConfig] = None) -> List[float]:
    """Generate 1024-dim vector embedding for a single text chunk via local Ollama."""
    res = get_embeddings_batch([text], config=config)
    return res[0] if res else []


def get_embeddings_batch(texts: List[str], config: Optional[NexusConfig] = None) -> List[List[float]]:
    """
    Generate vector embeddings in batch via local Ollama /api/embed.
    Uses hash caching to skip recomputation for identical chunks.
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
    missing_texts: List[str] = []

    for i, t in enumerate(texts):
        h = hash_text(t)
        if h in _EMBED_CACHE:
            results[i] = _EMBED_CACHE[h]
        else:
            missing_indices.append(i)
            clean_t = t.strip() if t and t.strip() else " "
            missing_texts.append(clean_t)

    if not missing_texts:
        return [r for r in results if r is not None]

    for batch_start in range(0, len(missing_texts), batch_size):
        batch_slice = missing_texts[batch_start:batch_start + batch_size]
        batch_orig_idx = missing_indices[batch_start:batch_start + batch_size]

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
                        for orig_i, txt, emb in zip(batch_orig_idx, batch_slice, embs):
                            results[orig_i] = emb
                            if len(_EMBED_CACHE) < MAX_CACHE_SIZE:
                                _EMBED_CACHE[hash_text(txt)] = emb
                        continue

                # Fallback to single /api/embeddings endpoint if /api/embed is not supported
                logger.warning(f"Ollama batch embed returned HTTP {resp.status_code}, falling back to legacy endpoint.")
                for orig_i, txt in zip(batch_orig_idx, batch_slice):
                    resp_single = client.post(
                        f"{ollama_url}/api/embeddings",
                        json={"model": model, "prompt": txt}
                    )
                    emb = resp_single.json().get("embedding", [])
                    results[orig_i] = emb
                    if len(_EMBED_CACHE) < MAX_CACHE_SIZE:
                        _EMBED_CACHE[hash_text(txt)] = emb
        except Exception as e:
            logger.warning(f"Failed to generate embeddings from {ollama_url}: {e}")
            for orig_i in batch_orig_idx:
                results[orig_i] = []

    return [r if r is not None else [] for r in results]
