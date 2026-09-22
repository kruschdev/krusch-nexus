# 🚀 KruschNexus

> **Universal Closed-Loop Document Ingestion & Corpus Indexing Engine**  
> *Air-gapped multi-format document ingestion, local OCR fallback, structural legal/business chunking, and PostgreSQL/pgvector indexing for the Krusch Homelab Ecosystem.*

---

## 🏛️ System Role in Homelab

**KruschNexus** is the foundational document ingestion backbone for the homelab. It solves the hardest part of local RAG: turning arbitrary messy user files (**PDF, scanned documents, DOCX, email archives, spreadsheets, HTML, markdown**) into clean, structure-aware, deduplicated vector embeddings with exact 1-based page numbers and section citations.

Specialized domain applications sit on top of KruschNexus:
- **⚖️ KruschLaw** (Legal Vertical): Legal research, attorney-client privileged matters, court filings, discovery exhibits, municipal codes, and 4-part legal brief synthesis.
- **🏢 KruschBiz** (See [`docs/KRUSCHBIZ_BLUEPRINT.md`](docs/KRUSCHBIZ_BLUEPRINT.md)): Confidential corporate data, P&L/financials, SOP execution checklists, policy-compliant email drafting, and role/SME discovery.

```
                    ┌────────────────────────────────────────────────────────┐
                    │                      KruschNexus                       │
                    │   (Universal Closed-Loop Document Ingestion Engine)     │
                    │  - Multi-Format Parsers (PDF + Local OCR, DOCX, EML)    │
                    │  - Structural Chunking (§, Headings, Deduplication)    │
                    │  - Safe Watch-Folder Archival (.ingested/)             │
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
- **PDF**: Page-by-page extraction via Poppler `pdftotext -layout` preserving exact 1-based page numbers.
- **Scanned PDF Fallback**: Pages with `< 30` selectable text characters trigger automatic local OCR (`pdftoppm -png -r 150` piped directly into host `tesseract-ocr` v5.3.4). Operates 100% offline in ~1.1s/page.
- **DOCX**: Native XML/ZIP parser extracting headings (`# Heading 1`, `## Heading 2`), tables, and paragraphs directly from `word/document.xml`.
- **EML (RFC822 Email)**: Native MIME parser extracting `Subject`, `From`, `To`, `Date`, and clean plain text / HTML bodies.
- **TXT / MD / HTML / CSV / JSON**: Encoding-resilient parsers with CSV table formatting and JSON prettification.

### 2. 🧩 Structural Chunking & SHA-256 Deduplication
- Breaks documents along structural boundaries (headings, sections like `§ 1950.5` / `Section 8.22.030` / `Article IV`, and paragraphs).
- Prepends context breadcrumbs to every chunk: `[{filename} - p.{page_number}] {header}`.
- Attaches strict provenance metadata: `page_number`, `chunk_index`, `source_hash` (chunk SHA-256), `doc_hash` (file SHA-256), `header`, and `doc_type` (`authority`, `work_product`, `fact_narrative`, `general`).
- File-level and chunk-level SHA-256 deduplication skips redundant compute.

### 3. 🛡️ Safe Watch-Folder Archival & Ingest Reports
- Continuous watch daemon monitoring `./ingest_watch/`.
- **Non-Destructive Archival**: Automatically moves processed files to `.ingested/` subfolders (never deletes source files with `os.remove`).
- **Comprehensive Ingest Report**: Stored in DB as JSON and returned on ingestion:
  ```json
  {
    "status": "completed",
    "document_id": 12,
    "filename": "lease_agreement.pdf",
    "workspace": "Matter_Smith",
    "pages_in": 14,
    "chunks_out": 28,
    "ocr_pages": [14],
    "duration_ms": 1450.2,
    "timestamp": "2026-09-22T19:30:00Z"
  }
  ```

### 4. ⚡ Closed-Loop Vector Persistence & Local Inference
- **PostgreSQL 16 + pgvector**: Dedicated `krusch_nexus_db` on `localhost:5432` with HNSW cosine indexes (`vector(1024)`) and GIN full-text search indexes (`tsvector`).
- **Local Ollama Embeddings**: `bge-large` 1024-dimensional embeddings via `http://127.0.0.1:11434` with batching and SHA-256 text caching.
- **Hybrid RRF Search**: Combines dense vector similarity and lexical ranking into Reciprocal Rank Fusion with exact `[filename, p. X, § Section]` citation footnotes.

---

## 🔌 Model Context Protocol (MCP) Tools

Connect any AI assistant (Claude Desktop, Cursor, Antigravity) to the ingestion engine:

- **`nexus_ingest_file(file_path, workspace_name, doc_type, archive)`**: Ingest a local file (PDF with OCR, DOCX, EML, CSV, HTML, TXT) and receive an Ingest Report.
- **`nexus_ingest_directory(directory_path, workspace_name, archive)`**: Batch-ingest all supported documents from a directory.
- **`nexus_get_ingest_report(doc_id_or_hash)`**: Retrieve processing metrics, page counts, OCR status, and chunk counts.
- **`nexus_search_corpus(query, workspace_name, doc_type, limit)`**: Execute hybrid vector + full-text search with exact citations.
- **`nexus_list_workspaces()` & `nexus_list_documents()`**: Explore corpus structure and indexed documents.

---

## 💻 Python Client Integration

External Python services (such as KruschLaw or CLI tools) can use `NexusIngestClient` directly:

```python
from src.backend.client import NexusIngestClient

client = NexusIngestClient()

# Ingest a file directly
report = client.ingest_file(
    filepath="/path/to/contract.pdf",
    workspace="ClientMatter_402",
    doc_type="authority"
)
print("Ingest report:", report)

# Search corpus with exact citations
results = client.search_corpus(
    query="liquidated damages clause",
    workspace="ClientMatter_402"
)
for r in results:
    print(r["citation"], r["content"][:100])
```

---

## 🧪 Testing & Verification

Run the test suites:
```bash
# Run unit test suite (parsers, sliding-window chunking, deduplication, client, MCP tools)
python3 -m unittest discover -s tests -p "test_*.py" -v

# Run integration tests against PostgreSQL and local Ollama
python3 -m unittest src.backend.test_closed_loop_ingest
python3 -m unittest src.backend.test_nexus_client_and_mcp
```

