# 🚀 KruschNexus

> **Universal Offline Document Ingestion Engine & Page-True Citation Spine**  
> *Air-gapped multi-format document ingestion, local OCR fallback, structural chunking, and PostgreSQL/pgvector indexing for the Krusch Homelab Ecosystem.*

---

## 🏛️ System Role in Homelab

**KruschNexus** is the foundational document ingestion and corpus indexing spine for the homelab. It solves the hardest part of local RAG: turning arbitrary messy user files (**PDF, scanned documents, DOCX, email archives, spreadsheets, HTML, markdown**) into clean, structure-aware, deduplicated vector embeddings with exact 1-based page numbers and section citations.

Specialized domain verticals and external agents talk to Nexus purely through the typed Python SDK, FastMCP tools, or the REST API:
- **⚖️ KruschLaw** (Legal Vertical): Legal research, attorney-client privileged matters, court filings, discovery exhibits, municipal codes, and 4-part legal brief synthesis.
- **🏢 KruschBiz** (Business Vertical): Corporate operations, financials, SOP execution checklists, policy-compliant email drafting, and role/SME discovery.

```
                    ┌────────────────────────────────────────────────────────┐
                    │                      KruschNexus                       │
                    │   (Universal Closed-Loop Document Ingestion Engine)     │
                    │  - Multi-Format Parsers (PDF + Local OCR, DOCX, EML)    │
                    │  - Structural Chunking (§, Headings, Deduplication)    │
                    │  - Watchdog Staging State Machine (.ingested/.failed)  │
                    │  - Local Embeddings (bge-large) & Hybrid PostgreSQL    │
                    └───────────┬────────────────────────────────┬───────────┘
                                │                                │
                                ▼                                ▼
           ┌──────────────────────────────┐ ┌──────────────────────────────────┐
           │          KruschLaw           │ │            KruschBiz             │
           │  (Legal Vertical)            │ │  (Business Vertical)            │
           │ - Attorney-Client Privilege  │ │ - Confidential Corporate Data    │
           │ - Municipal Codes & Case Law │ │ - P&L, Financials & Audit Memos  │
           │ - Court Exhibits & Discovery │ │ - Operational SOP Action Lists   │
           │ - 4-Part Legal Brief Format  │ │ - Policy-Compliant Email Drafts  │
           │ - Statutory Citation Scans   │ │ - Role Owners & SME Discovery    │
           └──────────────────────────────┘ └──────────────────────────────────┘
```

---

## 🌟 Key Capabilities

### 1. 📄 Multi-Format Ingestion Suite with Local OCR Fallback
- **PDF**: Page-by-page layout extraction via Poppler `pdftotext -layout` preserving exact 1-based page numbers.
- **Scanned PDF Fallback**: Pages with selectable characters below threshold trigger automatic local OCR (`pdftoppm -png` piped directly into host `tesseract-ocr`). Operates 100% offline.
- **DOCX**: Native XML/ZIP parser extracting headings (`# Heading 1`, `## Heading 2`), tables, and paragraphs directly from `word/document.xml`.
- **EML (RFC822 Email)**: Native MIME parser extracting `Subject`, `From`, `To`, `Date`, and clean plain text / HTML bodies.
- **TXT / MD / HTML / CSV / JSON**: Encoding-resilient parsers with CSV table formatting and JSON prettification.

### 2. 🧩 Structural Chunking & SHA-256 Deduplication
- Breaks documents along structural boundaries (headings, sections like `§ 1950.5` / `Section 8.22.030` / `Article IV`, and paragraphs).
- Context breadcrumbs attached to every chunk: `[{filename} - p.{page_number}] {header}`.
- Context sliding window overlap across page and chunk boundaries.
- Content-hash deduplication and alias handling for identical files uploaded with new names.

### 3. 🛡️ Hardened Watchdog State Machine & Failure Isolation
- Inotify file detection with staging locks (`staging/<workspace>/<file>.part`).
- Multi-stage pipeline: Hash check -> Parse -> Chunk -> Embed -> DB Write.
- **Success State**: Moves processed files safely to `.ingested/<workspace>/<hash>-<name>`.
- **Failure State**: Isolates corrupted/unparseable files to `.failed/<workspace>/<name>` with a detailed `<name>.error.json` sidecar (error class, message, stack trace, duration). Never leaves poison files in the watch folder to retry in loops.
- Bounded concurrency workers (default: 2 OCR jobs, 4 embed batches) preventing database stampedes.

### 4. ⚡ Hybrid Retrieval (Dense Vector + FTS + RRF + Section Boost)
- **PostgreSQL 16 + pgvector**: `krusch_nexus_db` on `localhost:5432` with HNSW cosine indexes (`vector(1024)`) and GIN full-text search indexes (`tsvector`).
- **Local Ollama Embeddings**: `bge-large` 1024-dimensional embeddings with batching and SHA-256 text caching.
- **Reciprocal Rank Fusion (RRF)**: Merges dense cosine similarity and lexical ranks with $k=60$.
- **Section Heading Boost**: Automatically boosts matching section headings when queries contain patterns like `§ 1950.5` or `Section 8.22`.
- **Canonical Footnotes**: Outputs ground-truth citations: `[filename, p. X, § Section]`.

---

## 💻 Public Python SDK

KruschNexus provides a clean, versioned SDK returning typed Pydantic models:

```python
from src.backend.config import NexusConfig
from src.backend.client import NexusIngestClient
from src.backend.models import IngestReport, SearchHit

config = NexusConfig(
    database_url="postgresql://krusch:password@localhost:5432/krusch_nexus_db",
    ollama_url="http://127.0.0.1:11434",
    embed_model="bge-large"
)
client = NexusIngestClient(config)

# 1. Ingest file into workspace
report: IngestReport = client.ingest_file(
    filepath="/path/to/contract.pdf",
    workspace="Matter_Smith",
    doc_type="authority"
)
print(f"Ingested {report.filename} ({report.total_pages} pages, {report.total_chunks} chunks)")

# 2. Hybrid search with canonical citations
hits: list[SearchHit] = client.search(
    query="liquidated damages clause under Section 14.1",
    workspace="Matter_Smith",
    limit=5
)
for hit in hits:
    print(f"Citation: {hit.citation} (RRF: {hit.rrf_score})")
    print(hit.content[:150])
```

---

## 🌐 HTTP REST API (Twin)

KruschNexus runs an air-gapped FastAPI service mirroring all SDK operations:

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Diagnostic healthcheck verifying DB, pgvector, and Ollama |
| `POST` | `/v1/ingest` | Multipart file upload or local filepath ingestion |
| `POST` | `/v1/search` | Hybrid search returning ranked `SearchHit`s |
| `GET` | `/v1/documents` | List indexed documents with workspace filtering |
| `GET` | `/v1/documents/{id}/report` | Retrieve stored `IngestReport` |
| `GET` | `/v1/workspaces` | List all document workspaces |
| `POST` | `/v1/workspaces` | Create an isolated workspace |

---

## 🔌 Model Context Protocol (MCP) Tools

The FastMCP server (`nexus-mcp`) exposes 6 canonical tools to AI agents (Claude Desktop, Cursor, Antigravity):

- **`nexus_ingest_file`**: Ingest a local file with OCR fallback and receive an Ingest Report.
- **`nexus_ingest_directory`**: Batch-ingest all supported documents from a directory.
- **`nexus_get_ingest_report`**: Retrieve metrics, page counts, OCR status, and chunk counts.
- **`nexus_search_corpus`**: Execute hybrid vector + FTS search with exact citations.
- **`nexus_list_workspaces`**: List document workspaces and counts.
- **`nexus_list_documents`**: List indexed documents in a workspace.

---

## 🧪 Testing & Grounded Evaluation

Run the full layered test suite:

```bash
# Run all 24 unit & state machine tests across all layers
./mcp_env/bin/python3 -m unittest discover tests

# Run the page-true citation evaluation benchmark (measures Recall@5)
./mcp_env/bin/python3 -m unittest tests/test_citation_eval.py
```

### Benchmark Results
- **Corpus**: Multi-page lease PDF, Scanned settlement PDF (OCR), Policy DOCX, Deal memo EML, Municipal code.
- **Metric**: **Recall@5 = 100.0%** (7/7 queries retrieved the exact correct page and section header).
