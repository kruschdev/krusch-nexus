# KruschNexus

> **Air-gapped document ingestion engine and page-true citation spine.**  
> *Deterministic parsers, structure-first chunking, local vector embeddings, and zero-hallucination citations.*

[![CI](https://github.com/kruschdev/krusch-nexus/actions/workflows/test.yml/badge.svg)](https://github.com/kruschdev/krusch-nexus/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Version: 0.2.3](https://img.shields.io/badge/version-0.2.3-green.svg)](spec.md)

---

## 1. What Problem Does This Solve?

**Citations die in local RAG pipelines.**

When standard vector pipelines ingest PDFs, DOCX files, and contracts, they strip pagination, ignore section hierarchies, and slice text by arbitrary character or token counts. By the time an LLM retrieves a chunk, the original page number, section header, and spatial bounding are lost. The model is forced to guess where the text came from—leading to phantom page citations, hallucinated statutes, and unverified assertions.

**KruschNexus guarantees citation truth:**
- **Page-True**: Extracts PDF text page-by-page. A hit on page 4 points to physical page 4.
- **Structure-First**: Preserves statutory subsection integrity (`§ 1950.5`, `Section 8.22.030`, `Art. IV`) and carries hierarchical heading stacks (`Article IV > Section 8.22.030`).
- **Explainable Hybrid Retrieval**: Combines pgvector dense cosine search with PostgreSQL full-text search (`tsvector`), statutory section boosting, quoted phrase matching (`"liquidated damages"`), hard tenant isolation (`workspace_id`), and explicit scoring breakdown (`vector_rank`, `fts_rank`, `section_boost`, `phrase_boost`, `score`).
- **Air-Gapped & Local**: Zero external API calls, zero telemetry, zero cloud egress. Runs entirely on local CPU/GPU with Ollama and PostgreSQL.

---

## 2. Requirements & Verification

KruschNexus relies on standard offline binaries for document rendering and local embeddings:

```bash
# 1. Poppler (page-at-a-time text extraction, scan rendering, metadata)
sudo apt-get install -y poppler-utils

# 2. Tesseract OCR (fallback for scanned documents and image exhibits)
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng

# 3. Ollama (local vector embeddings, default: bge-large, 1024 dimensions)
ollama pull bge-large

# 4. Verify environment with nexus doctor
nexus doctor
```

---

## 3. Quickstart: One PDF, One Search, Honest Citations

### Option A: Docker Compose (Recommended)

```bash
cp .env.example .env
# Set a secure POSTGRES_PASSWORD in .env

# Start core services (PostgreSQL/pgvector + FastAPI backend + worker + MCP)
docker compose up -d
```

### Option B: Python Virtual Environment

```bash
pip install -e ".[dev]"
alembic upgrade head
nexus doctor
```

### Ingest and Search with Python SDK

```python
from krusch_nexus import NexusClient, DocType

# Initialize client (uses DATABASE_URL and OLLAMA_EMBED_HOST from env)
client = NexusClient.from_env()

# 1. Ingest a document into an isolated workspace
report = client.ingest(
    filepath="sample_contract.pdf",
    workspace="Acquisition_2026",
    doc_type=DocType.AUTHORITY
)
print(f"Ingested {report.total_pages} pages, {report.total_chunks} chunks in {report.duration_ms:.1f}ms")

# 2. Search with page-true citation spine and exact phrase boost
hits = client.search('"commercial office space" Section 8.22', workspace="Acquisition_2026", limit=3)

for hit in hits:
    print(f"[{hit.filename}, p. {hit.page_number}, § {hit.header}] (score: {hit.score:.4f})")
    print(f"Text: {hit.text.strip()}\n")
```

**Real output:**
```text
[sample_contract.pdf, p. 1, § Section 8.22 Permitted Use of Premises] (score: 0.3825)
Text: COMMERCIAL LEASE AGREEMENT
Section 8.22 Permitted Use of Premises
Premises shall be used exclusively for commercial office space.
```

---

## 4. Unified OCR Policy

KruschNexus executes a strict, deterministic OCR policy:

1. **Digital Text Fast-Path**: Every page is rendered with Poppler `pdftotext -f N -l N -layout`.
2. **Selective OCR Trigger**: If a page yields $< 40$ printable characters **or** contains image XObjects (scanned exhibits), OCR fallback is triggered.
3. **High-Resolution Scanning**: Pages are rendered via `pdftoppm -r 300` and parsed using Tesseract with `--psm 6` (prose) and fallback to `--psm 4` (sparse legal forms) with language allowlist (`eng` default).
4. **Distinct Storage**: Digital text and OCR text are stored separately in `PageData` (`digital_text`, `ocr_text`) so documents can be re-OCRed without discarding clean digital extractions.
5. **Quality Confidence**: Word-level confidences are recorded per page (`ocr_confidence: {1: 0.92}`), low-confidence text is flagged with `LOW_OCR_CONFIDENCE`, and running headers/footers are suppressed.
6. **Encrypted PDFs**: Fail closed immediately with `EncryptedPdfError` (HTTP 422) instead of silently indexing blank pages.

---

## 5. What This Project Is NOT

To maintain architectural focus and operational reliability, KruschNexus is strictly bounded:

- ❌ **NOT a chat UI or chatbot**: There is no React webapp, chat input, or conversation history inside this repo.
- ❌ **NOT a legal advice engine**: It does not draft legal briefs or offer statutory interpretations.
- ❌ **NOT an agent coding harness or swarm**: It contains no agent loops or swarm orchestrators.
- ❌ **NOT a cloud SaaS**: It has no external telemetry, cloud model calls, or SaaS billing.

KruschNexus is a dedicated **corpus factory and citation spine**. Domain applications (such as legal platforms, research assistants, and compliance bots) consume KruschNexus as an external dependency via its public Python SDK (`from krusch_nexus import NexusClient`) or FastMCP server.

---

## 6. Public Python SDK & Data Models

KruschNexus exports a frozen, versioned public API surface:

```python
from krusch_nexus import (
    NexusClient,       # Canonical client
    NexusConfig,       # Configuration dataclass
    SearchHit,         # Versioned search hit with explainability fuse
    SearchFilter,      # Librarian predicates (page, header_regex, doc_id, doc_type)
    IngestReport,      # Ingest provenance report (hash, pages, ocr_confidence, manifest)
    StructuredLocator, # Structured locator model (kind, page, path, formatted)
    DocType,           # Enum: AUTHORITY, WORK_PRODUCT, FACT_NARRATIVE, GENERAL
    WarningCode        # Enum: ENCRYPTED_SKIPPED, OCR_EMPTY_PAGE, TRUNCATED, etc.
)
```

---

## 7. Model Context Protocol (MCP)

KruschNexus includes a native FastMCP server exposing 6 canonical user tools and operator-gated maintenance tools:

```bash
# Launch FastMCP stdio server
nexus-mcp

# Or via CLI
nexus mcp
```

### Available Tools

- `nexus_list_workspaces()`: List all document workspaces and document counts.
- `nexus_list_documents(workspace)`: List documents in a workspace.
- `nexus_ingest_file(file_path, workspace_name, doc_type, archive)`: Ingest a single file with page-true provenance.
- `nexus_get_ingest_report(doc_id_or_hash)`: Retrieve detailed ingest report and provenance.
- `nexus_search_corpus(query, workspace_name, doc_type, limit)`: Execute hybrid search with structured citations.
- `nexus_doctor()`: Run environment diagnostic audit.
- `nexus_reparse(document_id, operator_confirmed)`: Operator-gated document reparse.
- `nexus_delete_document(document_id, operator_confirmed)`: Operator-gated document deletion.

---

## 8. Honest Evaluation Harness & Multi-Suite Benchmarks

KruschNexus partitions verification into four explicit suites (documented in detail in [Evaluation Methodology](docs/eval.md)):

| Suite | Focus | Queries / Tests | Release Gate Requirement | Measured Result |
|---|---|---|---|---|
| **`eval_regression`** | Frozen fixtures invariant lock | 60 queries | Recall@5 = 100%, Citation $\ge 80.0\%$ | **Recall@5 = 100.0%**<br>Citation Acc = 98.3%<br>MRR = 0.989 |
| **`eval_heldout`** | Unseen legal instruments & spans | 25 queries | Recall@5 $\ge 85.0\%$, Citation $\ge 80.0\%$, Span $\ge 80.0\%$ | **Recall@5 = 100.0%**<br>Citation Acc = 96.0%<br>Span Precision = 96.0% |
| **`eval_adversarial`** | Real PDFs (two-column, redline, fax/stamp) | 6 scenarios | Zero unhandled exceptions; Fail-closed; CER $\le 45\%$ | **0 exceptions**<br>Fail-closed verified<br>CER/WER passed |
| **`eval_isolation`** | Cross-workspace multi-tenant probes | 75 checks | 0.0000% cross-tenant leakage | **0.0000% leakage**<br>(0/75 probes) |

*Note: Recall@5, Citation Accuracy, and Span Precision are strictly decoupled. A retrieved chunk that appears on the wrong page fails Citation Accuracy even if document retrieval succeeds.*

### Run Benchmark Suites & Generate Machine-Readable Report

```bash
# Run all evaluation suites
pytest tests/eval/ -v -s

# Generate machine-readable eval_report.json
python -m krusch_nexus.eval_report
```

---

## 9. Architecture & Documentation

- [Evaluation Methodology & OCR Benchmark](docs/eval.md) — 4-suite architecture and scoring methodology.
- [Security & Threat Model](docs/security_and_threat_model.md) — Localhost binding, sandbox paths, fail-closed passwords.
- [Homelab Ecosystem Context](docs/ecosystem.md) — Fleet node mapping and upstream domain consumer boundaries.
- [MCP Server Specification](docs/MCP_SERVER.md) — Complete tool signatures and SSE transport options.
- [Agent Setup Guide](docs/AGENT_SETUP_GUIDE.md) — Integration guide for Cursor, Claude Desktop, and IDE agents.
