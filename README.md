# KruschNexus

> **Air-gapped document ingestion engine and page-true citation spine.**  
> *Deterministic parsers, structure-first chunking, local vector embeddings, and zero-hallucination citations.*

[![CI](https://github.com/kruschdev/krusch-nexus/actions/workflows/test.yml/badge.svg)](https://github.com/kruschdev/krusch-nexus/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

---

## 1. What Problem Does This Solve?

**Citations die in local RAG pipelines.**

When standard vector pipelines ingest PDFs, DOCX files, and contracts, they strip pagination, ignore section hierarchies, and slice text by arbitrary character or token counts. By the time an LLM retrieves a chunk, the original page number, section header, and spatial bounding are lost. The model is forced to guess where the text came from—leading to phantom page citations, hallucinated statutes, and unverified assertions.

**KruschNexus guarantees citation truth:**
- **Page-True**: Extracts PDF text page-by-page. A hit on page 4 points to physical page 4.
- **Structure-First**: Preserves statutory subsection integrity (`§ 1950.5`, `Section 8.22`, `Article IV`) and carries hierarchical heading stacks (`Article IV > Section 8.22.030`).
- **Explainable Hybrid Retrieval**: Combines pgvector dense cosine search with PostgreSQL full-text search (`tsvector`), statutory citation query rewriting, hard tenant isolation (`workspace_id`), and explicit scoring breakdown (`vector_rank`, `fts_rank`, `section_boost`, `rrf_score`).
- **Air-Gapped & Local**: Zero external API calls, zero telemetry, zero cloud egress. Runs entirely on local CPU/GPU with Ollama and PostgreSQL.

---

## 2. What You Must Install

KruschNexus relies on standard offline binaries for document rendering and local embeddings:

```bash
# 1. Poppler (page-at-a-time text extraction, scan rendering, metadata)
sudo apt-get install -y poppler-utils

# 2. Tesseract OCR (fallback for scanned documents and image exhibits)
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng

# 3. Ollama (local vector embeddings, default: bge-large, 1024 dimensions)
ollama pull bge-large

# 4. PostgreSQL 16 + pgvector (or run via Docker Compose)
```

---

## 3. Quickstart: One PDF, One Search, Honest Citations

### Option A: Docker Compose (Recommended)

Start the core database and API services:

```bash
cp .env.example .env
# Set a secure POSTGRES_PASSWORD in .env

# Start core services (PostgreSQL/pgvector + FastAPI backend)
docker compose --profile core up -d
```

### Option B: Python Virtual Environment

```bash
pip install -e ".[dev]"
alembic upgrade head
```

### Ingest and Search in 3 Lines of Python

```python
from krusch_nexus import Nexus, DocType

# Initialize client (uses DATABASE_URL and OLLAMA_EMBED_HOST from env)
nexus = Nexus.from_env()

# 1. Ingest a document into an isolated workspace
report = nexus.ingest(
    filepath="sample_contract.pdf",
    workspace="Acquisition_2026",
    doc_type=DocType.AUTHORITY
)
print(f"Ingested {report.pages} pages, {report.chunks} chunks in {report.duration_ms:.1f}ms")

# 2. Search with page-true citation spine
hits = nexus.search("permitted commercial office use", workspace="Acquisition_2026", limit=3)

for hit in hits:
    print(f"[{hit.filename}, p. {hit.page_number}, § {hit.header}] (score: {hit.score:.4f})")
    print(f"Text: {hit.text.strip()}\n")
```

**Real output:**
```text
[sample_contract.pdf, p. 1, § Section 8.22 Permitted Use of Premises] (score: 0.2825)
Text: COMMERCIAL LEASE AGREEMENT
Section 8.22 Permitted Use of Premises
Premises shall be used for commercial office space.
```

---

## 4. When Does OCR Kick In?

KruschNexus does **not** waste GPU cycles running OCR on clean digital PDFs. It uses a page-by-page inspection heuristic:

1. **Digital Fast-Path**: Each page is inspected with `pdftotext -f N -l N`. If the page yields $\ge 30$ printable characters, native vector text is extracted with exact page numbering.
2. **Selective OCR Fallback**: If a page contains $< 30$ printable characters (or embedded raster scans), KruschNexus renders the page at 300 DPI via `pdftoppm` and runs Tesseract (`--oem 1 --psm 4`).
3. **Confidence Scoring**: Tesseract TSV word-level confidences are recorded on each chunk (`confidence: 0.0 - 1.0`). Low-confidence garbage is flagged, and running headers/footers are suppressed.
4. **Encrypted PDFs**: Fails closed immediately via `pdfinfo` inspection with `EncryptedPdfError` instead of indexing empty pages.

---

## 5. What This Project Is NOT

To maintain architectural focus and operational reliability, KruschNexus is strictly bounded:

- ❌ **NOT a chat UI or chatbot**: There is no React webapp, chat input, or conversation history inside this repo.
- ❌ **NOT a legal advice engine**: It does not draft legal briefs or offer statutory interpretations.
- ❌ **NOT an agent coding harness**: It contains no agent loop or autonomous tool execution logic.
- ❌ **NOT a cloud SaaS**: It has no billing, multi-tenant billing meters, or external SaaS dependencies.

KruschNexus is a dedicated **corpus factory and citation spine**. Domain applications (such as legal tools, research assistants, and compliance bots) consume KruschNexus as an external dependency via its public Python SDK (`from krusch_nexus import Nexus`) or FastMCP server.

---

## 6. Public Python SDK & Data Models

KruschNexus exports a frozen, versioned public API surface:

```python
from krusch_nexus import (
    Nexus,             # Canonical client
    NexusClient,       # Client alias
    NexusConfig,       # Configuration dataclass
    SearchHit,         # Versioned search result with explainability fuse
    SearchFilter,      # Librarian predicates (page, header_regex, doc_id, doc_type)
    IngestReport,      # Ingest provenance report (hash, pages, ocr_pages, manifest)
    DocType,           # Enum: AUTHORITY, WORK_PRODUCT, CORRESPONDENCE, FINANCIAL, GENERAL
)
```

### Search with Librarian Filters & Explainability

```python
from krusch_nexus import Nexus, SearchFilter, DocType

nexus = Nexus.from_env()

# Search with hard predicates (page, header regex, document type)
hits = nexus.search(
    query="liquidated damages",
    workspace="Settlement_Matter",
    doc_type=DocType.AUTHORITY,
    filters=SearchFilter(page=1, header_regex=r"14\.1|Liquidated"),
    limit=5
)

# Inspect the explainability fuse
for h in hits:
    print(f"Hit: {h.citation}")
    print(f"RRF Score: {h.score:.4f} | Dense Rank: {h.vector_rank} | FTS Rank: {h.fts_rank}")
    print(f"Section Boost: {h.section_boost} | Lexical Boost: {h.lexical_boost}")
    print(f"Match Reasons: {h.match_reasons}")
```

### Operator Reindexing Command

When upgrading chunking rules or switching embedding models (e.g. from `bge-large` to `nomic-embed-text`), reindex without re-reading source files:

```bash
# Reindex entire workspace
nexus reindex --workspace Acquisition_2026 --model bge-large

# Reindex single document
nexus reindex --document-id 42
```

---

## 7. Model Context Protocol (MCP)

KruschNexus includes a native FastMCP server for direct integration with **Claude Desktop**, **Cursor**, and **Antigravity IDE**:

```bash
# Launch FastMCP stdio server
nexus-mcp

# Or run via script
./scripts/run_mcp.sh
```

### Configuration (`claude_desktop_config.json` or `.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "krusch-nexus": {
      "command": "nexus-mcp",
      "env": {
        "DATABASE_URL": "postgresql://user:password@localhost:5432/krusch_nexus_db",
        "OLLAMA_EMBED_HOST": "http://127.0.0.1:11434"
      }
    }
  }
}
```

---

## 8. Frozen Citation Benchmark

KruschNexus gates releases on an automated 60-query multi-format benchmark (`tests/eval/test_citation_eval.py`) across scanned PDFs, vector contracts, DOCX policies, EML threads, municipal codes, and CSV matrices:

```bash
pytest tests/eval/test_citation_eval.py -v -s
```

### Release Gating Results

| Metric | Release Gate | Measured Result | Status |
|---|---|---|---|
| **Recall@5** (60 frozen queries) | $\ge 92.0\%$ | **100.0%** (60/60) | **PASS** |
| **Page / Locator Accuracy** | $\ge 85.0\%$ | **100.0%** (60/60) | **PASS** |
| **Section Header Accuracy** | $\ge 85.0\%$ | **100.0%** (60/60) | **PASS** |
| **Snippet Containment** | $\ge 85.0\%$ | **100.0%** (60/60) | **PASS** |
| **OCR Page Recall** (300 DPI Tesseract) | $100.0\%$ | **100.0%** (8/8) | **PASS** |
| **Negative & Distractor Resistance** | $100.0\%$ | **100.0%** (0 false hits) | **PASS** |
| **Cross-Workspace Leakage Rate** | $0.00\%$ | **0.00%** (zero leakage) | **PASS** |

---

## 9. Architecture & Documentation

- [Security & Threat Model](docs/security_and_threat_model.md) — Localhost binding, sandbox paths, fail-closed passwords.
- [Homelab Ecosystem Context](docs/ecosystem.md) — Fleet node mapping and upstream domain consumer boundaries.
- [MCP Server Specification](docs/MCP_SERVER.md) — Complete tool signatures and SSE transport options.
- [Agent Setup Guide](docs/AGENT_SETUP_GUIDE.md) — Integration guide for Cursor, Claude Desktop, and IDE agents.
