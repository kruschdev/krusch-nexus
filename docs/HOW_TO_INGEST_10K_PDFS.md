# KruschNexus — How to Ingest 10,000 PDFs (`HOW_TO_INGEST_10K_PDFS.md`)

> **Operational Runbook**: High-Throughput Batch Ingestion, PostgreSQL 16 Tuning, VRAM Management, and Fault-Tolerant Resumption for Large Local Corpora.

---

## 1. High-Throughput Architecture Overview

Ingesting 10,000 PDFs (~50,000–100,000 pages / ~250,000 vector chunks) on local hardware requires balancing CPU parallelism (Poppler text extraction + Tesseract OCR) with GPU/VRAM throughput (vector embeddings) and PostgreSQL transaction throughput.

```
┌────────────────────────────────────────────────────────┐
│                   Input Directory                      │
│                  (10,000 PDF files)                    │
└───────────┬────────────────────────────────┬───────────┘
            │                                │
            ▼                                ▼
┌───────────────────────┐        ┌───────────────────────┐
│ CPU Worker Pool (4-8) │        │  Path Sandbox & MIME  │
│ Poppler pdftotext/ppm │        │   Magic-Byte Gate     │
└───────────┬───────────┘        └───────────┬───────────┘
            │                                │
            ▼                                ▼
┌────────────────────────────────────────────────────────┐
│ Batch Embedding (32 chunks/batch) via bge-large (GPU)  │
│ Persistent Disk Cache: ~/.cache/krusch_nexus/*.sqlite  │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│ Atomic PostgreSQL 16 Transaction (Document + Chunks)   │
│ IngestRun Ledger & Lineage Resolution                 │
└────────────────────────────────────────────────────────┘
```

---

## 2. Production Batch Ingestion Script

Save as `scripts/bulk_ingest_10k.py`:

```python
#!/usr/bin/env python3
"""
scripts/bulk_ingest_10k.py
High-throughput, concurrent batch ingestion with progress tracking and failure logging.
"""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from krusch_nexus import NexusClient, NexusConfig, DocType

WORKSPACE = "CorporateCorpus_2026"
SOURCE_DIR = "/mnt/data/contracts"
CONCURRENCY = 6  # Optimal for 12-core / 24-thread CPU
EMBED_BATCH = 32

config = NexusConfig(
    embed_model="bge-large",
    embed_dim=1024,
    embed_batch_size=EMBED_BATCH,
    subprocess_timeout=45.0,  # Guard against corrupt PDF hanging
    allowed_ingest_roots=[SOURCE_DIR]
)
client = NexusClient(config=config)

def ingest_single(pdf_path: Path) -> dict:
    try:
        report = client.ingest(
            filepath=str(pdf_path),
            workspace=WORKSPACE,
            doc_type=DocType.AUTHORITY
        )
        return {
            "file": pdf_path.name,
            "status": report.status,
            "pages": report.total_pages,
            "chunks": report.total_chunks,
            "duration_ms": report.duration_ms,
            "breakdown": report.duration_breakdown_ms
        }
    except Exception as e:
        return {"file": pdf_path.name, "status": "error", "error": str(e)}

def main():
    pdf_files = list(Path(SOURCE_DIR).glob("**/*.pdf"))
    total_files = len(pdf_files)
    print(f"Discovered {total_files} PDF files in '{SOURCE_DIR}'")

    start_time = time.time()
    completed = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        futures = {executor.submit(ingest_single, p): p for p in pdf_files}
        for future in as_completed(futures):
            res = future.result()
            completed += 1
            if res["status"] in ("completed", "skipped_duplicate"):
                print(f"[{completed}/{total_files}] OK: {res['file']} ({res.get('chunks', 0)} chunks in {res.get('duration_ms', 0):.0f}ms)")
            else:
                failed += 1
                print(f"[{completed}/{total_files}] FAIL: {res['file']} -> {res.get('error')}", file=sys.stderr)

    elapsed_s = time.time() - start_time
    print(f"\nFinished: {completed - failed} succeeded, {failed} failed in {elapsed_s:.1f}s ({total_files / elapsed_s:.2f} docs/sec)")

if __name__ == "__main__":
    main()
```

---

## 3. PostgreSQL 16 & pgvector Tuning

Before ingesting 10,000 PDFs, apply these optimizations to `postgresql.conf`:

```ini
# Memory Configuration (Assuming 32GB RAM Host)
shared_buffers = 8GB
work_mem = 64MB
maintenance_work_mem = 2GB
effective_cache_size = 24GB

# Write-Ahead Log (WAL) for Bulk Ingestion
wal_level = minimal
max_wal_size = 16GB
checkpoint_completion_target = 0.9

# Parallel Workers
max_worker_processes = 8
max_parallel_workers = 8
max_parallel_maintenance_workers = 4
```

> **HNSW Index Building Strategy**:
> If ingesting >50,000 chunks from scratch, **drop the HNSW vector index before ingestion** and rebuild it afterward. Building HNSW incrementally slows down commits by 3x–5x.
> ```sql
> -- 1. Drop index prior to bulk run:
> DROP INDEX IF EXISTS idx_document_chunks_embedding;
>
> -- 2. Execute bulk ingest script...
>
> -- 3. Rebuild HNSW index concurrently with high maintenance_work_mem:
> SET maintenance_work_mem = '4GB';
> CREATE INDEX idx_document_chunks_embedding 
> ON document_chunks USING hnsw (embedding vector_cosine_ops) 
> WITH (m = 16, ef_construction = 64);
> ```

---

## 4. Hardware Sizing & VRAM Budgets

| Component | Minimum Sizing | Recommended Fleet Profile |
|---|---|---|
| **CPU** | 8 Cores (i7 / Ryzen 7) | 16–20 Cores (Xeon E5 / Threadripper) |
| **RAM** | 32 GB DDR4 | 64 GB DDR4 |
| **GPU / VRAM** | RTX 2080 Ti (11 GB VRAM) | Dual RTX 2080 Ti or RTX 3060 (12–24 GB) |
| **Disk** | NVMe SSD (PCIe 3.0 / 4.0) | Dedicated NVMe data mount (`/mnt/nvme`) |

* **Ollama VRAM**: `bge-large` 1024d requires ~2.6 GB VRAM. Set `OLLAMA_NUM_PARALLEL=4` in systemd environment to allow concurrent embedding batches without serialization bottlenecks.
* **Disk Caching**: In-flight embeddings are automatically cached to SQLite (`~/.cache/krusch_nexus/embed_cache.sqlite`). Re-running ingestion or reprocessing identical sections costs **0 GPU compute**.

---

## 5. Fault-Tolerant Resumption & Idempotency

KruschNexus computes a composite idempotency key for every document:
$$\text{Key} = (\text{workspace\_id}, \text{file\_hash}, \text{parser\_version}, \text{embed\_model}, \text{embed\_dim})$$

If a bulk run is interrupted by power loss, kernel OOM, or thermal emergency:
1. Re-run `python scripts/bulk_ingest_10k.py`.
2. Already-committed documents are recognized via `file_hash` and return `status="skipped_duplicate"` in **<0.8ms**.
3. Incompletely committed documents (documents killed during embed or commit stages) are automatically rolled back and resumed cleanly.
