# ⚡ KruschNexus

> **Air-Gapped Universal Document Ingestion Engine & Citation Spine**  
> *Version 0.2.0*  
> *A dedicated corpus factory: file → parse → chunk with provenance → embed locally → persist → hybrid search with citations.*

---

## 1. Prerequisites (Host Binaries)

KruschNexus operates 100% offline and requires the following host binaries:

- **Poppler Utilities** (`pdftotext`, `pdftoppm`, `pdfinfo`): For page-at-a-time PDF text extraction, metadata inspection, and scan rendering.
  ```bash
  sudo apt-get install poppler-utils
  ```
- **Tesseract OCR** (`tesseract` + `tesseract-ocr-eng`): For high-resolution 300 DPI local OCR fallback on scanned pages.
  ```bash
  sudo apt-get install tesseract-ocr tesseract-ocr-eng
  ```
- **Ollama**: Local embedding host serving `bge-large` (1024 dimensions).
  ```bash
  ollama pull bge-large
  ```
- **PostgreSQL 16 with pgvector**: Relational storage with HNSW cosine (`vector(1024)`) and GIN `tsvector` indexes. Migrations managed via Alembic.

---

## 2. Quickstart

### Option A: Bare-Metal / Local Virtualenv
```bash
# 1. Install package in editable mode
pip install -e .

# 2. Run migrations
alembic upgrade head

# 3. Run test and eval suites
pytest -v
pytest tests/eval/test_citation_eval.py -s

# 4. Start the watch daemon or MCP server
nexus-daemon
nexus-mcp
```

### Option B: Docker Compose
```bash
# 1. Configure environment passwords
cp .env.example .env
# Edit .env and set a secure POSTGRES_PASSWORD

# 2. Launch PostgreSQL, FastAPI, Ingestion Worker, and FastMCP
docker compose up -d

# 3. Verify air-gap and local node connectivity
docker compose exec backend nexus verify --offline
```

*Note: In Docker Compose, the API (`127.0.0.1:8000`) and FastMCP server (`127.0.0.1:8002`) bind strictly to localhost by default.*

---

## 3. Citation Posture: Honest Provenance

Page-true citations only exist where the underlying format supports fixed pagination. KruschNexus enforces strict citation truth across all supported formats:

| Format | Page Fidelity | Citation Format | Behavior |
|---|---|---|---|
| **PDF (Digital)** | Page-True | `contract.pdf p.3 Section 8.22 Permitted Use` | Extracted page-at-a-time via `pdftotext -f N -l N`. |
| **PDF (Scanned)** | Page-True | `release.pdf p.1 Section 14.1 Liquidated Damages` | High-res 300 DPI Tesseract OCR fallback with `--psm 4/6`. |
| **PDF (Encrypted)**| Fail-Closed | `EncryptedPdfError` | Detects encrypted streams via `pdfinfo` and halts ingestion. |
| **DOCX** | Structural Locator | `policy.docx Section 1 > Section 1.2: Backup Retention` | Emits `page_number=None`. Walks XML body in single pass; tracks hierarchical heading stacks. |
| **EML** | Structural Locator | `deal.eml Section 4.5 of the Purchase Agreement` | Emits `page_number=None`. Decodes RFC2047 MIME headers and extracts attachments. |
| **CSV** | Row-Group Locator | `matrix.csv Rows 1-30` | Emits `page_number=None`. Chunks data by row groups with replayed table headers. |
| **Plain Text / Code**| Page 1 / Locator | `statute.txt p.1 § 1950.5 Security Deposits` | Emits 1-based page for single documents. |

---

## 4. Drop a File to Ingest

Drop any supported file (**PDF, DOCX, EML, CSV, HTML, TXT, MD**) into a workspace subdirectory inside `ingest_watch/`:

```bash
mkdir -p ingest_watch/Matter_Smith
cp /path/to/lease_agreement.pdf ingest_watch/Matter_Smith/
```

### Ingestion State Machine
Incoming files progress through an explicit 8-stage state machine:
`DETECTED → STAGED → HASHED → PARSED → CHUNKED → EMBEDDED → COMMITTED → ARCHIVED`

1. **Atomic DB Transactions**: The document record, structural chunks, and ingest report are committed in a single database transaction before the source file is moved to `.ingested/`.
2. **Poison File Isolation**: Corrupt, oversized, or unparseable files are immediately moved to `.failed/<workspace>/<file>` with a redacted `.error.json` sidecar. Error logs never contain raw document text.
3. **Stale-Lock Reaper**: A background daemon routine sweeps `staging/` and reaps any `.part` locks older than 10 minutes to prevent crash deadlocks.

### Ingest Report (Sample Output)
```json
{
  "status": "completed",
  "document_id": 42,
  "filename": "lease_agreement.pdf",
  "workspace": "Matter_Smith",
  "file_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "doc_type": "authority",
  "pages": 14,
  "chunks": 28,
  "ocr_pages": [1],
  "duration_ms": 342.15,
  "citation_preview": "lease_agreement.pdf p.1 § 8.22 Permitted Use of Premises"
}
```

---

## 5. Python SDK (`krusch_nexus`)

```python
from krusch_nexus import NexusClient, DocType

client = NexusClient.from_env()

# Ingest document
report = client.ingest(
    filepath="/data/lease.pdf",
    workspace="Matter_Smith",
    doc_type=DocType.AUTHORITY
)

# Search with hybrid vector + FTS and statutory section boost
hits = client.search("liquidated damages", workspace="Matter_Smith", limit=5)
for hit in hits:
    print(f"Citation: {hit.citation} (Score: {hit.score})")
    print(hit.text)

# Day-two operator workflows
client.reparse(document_id=report.document_id)
client.delete_document(document_id=report.document_id)
```

---

## 6. Model Context Protocol (MCP) Configuration

To connect KruschNexus directly to **Claude Desktop**, **Cursor**, or any MCP-compatible agent:

### Claude Desktop (`claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "krusch-nexus": {
      "command": "nexus-mcp",
      "env": {
        "DATABASE_URL": "postgresql://krusch:your_password@localhost:5432/krusch_nexus_db",
        "OLLAMA_EMBED_HOST": "http://127.0.0.1:11434"
      }
    }
  }
}
```

### Available Tools:
- `nexus_search_corpus(query, workspace_name, limit=5)`: Hybrid search with canonical citations.
- `nexus_ingest_file(file_path, workspace_name, doc_type)`: Ingest a document file into an isolated workspace.
- `nexus_reparse(document_id)`: Re-parse and re-chunk an existing document in the corpus.
- `nexus_delete_document(document_id)`: Delete a document and its chunks from the database.
- `nexus_get_ingest_report(doc_id_or_hash)`: Retrieve detailed ingestion provenance and OCR stats.
- `nexus_list_workspaces()`: List workspaces and indexed document counts.
- `nexus_list_documents(workspace_name)`: List documents within a workspace.

---

## 7. Security & Air-Gap Guarantees

- **Localhost Default**: All API and MCP services bind strictly to `127.0.0.1`.
- **API Token Authentication**: Optional bearer token security enabled via `NEXUS_API_TOKEN`.
- **Tenant Isolation**: Every database retrieval query mandates an explicit `workspace_id`.
- **Cloud Egress Protection**: Startup refuses to run when `AIRGAP=1` and external cloud keys (e.g. `OPENROUTER_API_KEY`) are present in the environment.
- **Privacy Redaction**: Error sidecars and logger outputs never emit raw document text.

---

## 8. Benchmark & Citation Evaluation

KruschNexus replaces unverified marketing claims with a frozen 25-query evaluation suite (`tests/eval/test_citation_eval.py`) run against a heterogeneous multi-format test fixture corpus (`tests/fixtures/`):

```bash
pytest tests/eval/test_citation_eval.py -v
```

### Verified Benchmark Metrics

| Metric | Target | Measured Result | Status |
|---|---|---|---|
| **Recall@5** (25 queries) | $\ge 90.0\%$ | **100.0%** (25/25) | **PASS** |
| **Header Accuracy** | $\ge 85.0\%$ | **100.0%** (25/25) | **PASS** |
| **OCR-Page Recall** (Scanned PDFs) | $\ge 90.0\%$ | **100.0%** (3/3) | **PASS** |
| **Page / Locator Accuracy** | $\ge 85.0\%$ | **92.0%** (23/25) | **PASS** |
| **Cross-Workspace Leakage** | $0.00\%$ | **0.00%** | **PASS** |

---

## 9. 30-Day Sequence & Architecture Roadmap

The KruschNexus development roadmap prioritizes spine correctness over premature feature expansion:

- **Week 1 — Subtract (Completed)**: Deleted legacy prototypes and SaaS cruft; unified package under `src/krusch_nexus/`; added Alembic schema migrations; bound all services to localhost; aligned documentation with honest `0.2.0` versioning.
- **Week 2 — Ingest Correctness (Completed)**: Implemented page-at-a-time PDF parsing (`pdftotext -f N -l N`); fail-closed encryption checks; single-file atomic DB transactions; hierarchical heading stacks; separated embedded raw text from citation display metadata.
- **Week 3 — Retrieve & Eval (Completed)**: Replaced complex retrieval cascades with a 150-line deterministic hybrid search engine (`retrieve.py`); built frozen 25-query evaluation fixture suite; verified contract tests across REST and FastMCP interfaces.
- **Week 4 — Ops & Hardening (Completed)**: `NEXUS_API_TOKEN` bearer auth; strict tenant workspace keying on every query; stale-lock reaper for orphaned `.part` locks; parser versioning and re-ingest operator commands (`reparse`, `delete_document`).
