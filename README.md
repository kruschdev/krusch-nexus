# KruschNexus

> **Status**: v0.2.3 — usable spine, small corpus

[![CI](https://github.com/kruschdev/krusch-nexus/actions/workflows/test.yml/badge.svg)](https://github.com/kruschdev/krusch-nexus/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Version: 0.2.3](https://img.shields.io/badge/version-0.2.3-green.svg)](spec.md)

---

## 1. What Problem Does This Solve?

**Citations die in local RAG pipelines.**

When standard vector pipelines ingest PDFs, DOCX files, and contracts, they strip pagination, ignore section hierarchies, and slice text by arbitrary character or token counts. By the time an LLM retrieves a chunk, the original page number, section header, and spatial bounding are lost. The model is forced to guess where the text came from—leading to phantom page citations, hallucinated statutes, and unverified assertions.

**KruschNexus is a citation-preserving ingest and search engine:**
- **Page-Faithful**: Extracts PDF text page-by-page. A hit on page 4 points to physical page 4.
- **Structure-First**: Preserves statutory subsection integrity (`§ 1950.5`, `Section 8.22.030`, `Art. IV`) and carries hierarchical heading stacks (`Article IV > Section 8.22.030`).
- **Explainable Hybrid Retrieval**: Combines pgvector dense cosine search with PostgreSQL full-text search (`tsvector`), statutory section boosting, quoted phrase matching (`"liquidated damages"`), hard tenant isolation (`workspace_id`), and explicit scoring breakdown (`vector_rank`, `fts_rank`, `section_boost`, `phrase_boost`, `score`, `score_vector`).
- **Air-Gapped & Local**: Zero external API calls, zero telemetry, zero cloud egress. Runs entirely on local CPU/GPU with Ollama and PostgreSQL.

---

## 2. Hardware Sizing & Minimal Requirements

- **Library Mode (Zero DB, Zero Daemons)**: 512 MB RAM, runs anywhere Python 3.11+ is installed. Parse, chunk, and extract page-faithful citations directly into JSONL in-memory.
- **CPU Ingest/Search**: 8 GB RAM, 10 GB disk, dual-core CPU. Runs full PostgreSQL + pgvector + Poppler + Ollama on local CPU without GPU hardware.
- **GPU Homelab Factory**: 16 GB RAM, NVIDIA RTX GPU (8+ GB VRAM) for accelerated batch embeddings and high-DPI OCR preprocessing.

---

## 3. Requirements & Verification

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

## 4. Five-Command Happy Path

```bash
# 1. Start core services (PostgreSQL/pgvector + FastAPI + worker + MCP)
docker compose up -d

# 2. Verify environment, storage quota, and operational limits
nexus doctor

# 3. Ingest document fixture into isolated workspace
nexus ingest tests/fixtures/sample_contract.pdf --workspace demo

# 4. Search with fail-closed hybrid retrieval
nexus search "commercial office space" --workspace demo

# 5. Inspect citation and explainability scoring breakdown
nexus explain "commercial office space" --workspace demo
```

**Real CLI output:**
```text
Found 1 hit(s) in workspace 'demo':
============================================================

[1] Citation: sample_contract.pdf, p. 1, Section 8.22 Permitted Use of Premises [chars: 45-210] (Score: 0.3825)
    Header:   Section 8.22 Permitted Use of Premises
    Match:    section_match, phrase_match, hybrid_rrf
    Content:  COMMERCIAL LEASE AGREEMENT Section 8.22 Permitted Use of Premises Premises shall be used exclusively for commercial office space...

============================================================
```

### Python SDK Usage

```python
from krusch_nexus import NexusClient, DocType

# Initialize client from environment
client = NexusClient.from_env()

# Ingest document into an isolated workspace
report = client.ingest(
    filepath="sample_contract.pdf",
    workspace="Acquisition_2026",
    doc_type=DocType.AUTHORITY
)
print(f"Ingested {report.total_pages} pages, {report.total_chunks} chunks in {report.duration_ms:.1f}ms")

# Search with page-true citation spine and exact phrase boost
hits = client.search('"commercial office space" Section 8.22', workspace="Acquisition_2026", limit=3)
for hit in hits:
    print(f"[{hit.citation}] (score: {hit.score:.4f}, reasons: {hit.match_reasons})")
    print(f"Text: {hit.text.strip()}\n")
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

### Available Tools (10 Canonical Tools)

**User Tools (8):**
- `nexus_list_workspaces(token)`: List document workspaces and indexed counts.
- `nexus_list_documents(workspace_name, token)`: List documents within a workspace.
- `nexus_ingest_file(file_path, workspace_name, doc_type, archive, token)`: Ingest a single file with page-faithful provenance.
- `nexus_ingest_directory(directory_path, workspace_name, doc_type, recursive, token)`: Ingest all supported documents from a directory.
- `nexus_get_ingest_report(doc_id_or_hash)`: Retrieve detailed ingest report and provenance.
- `nexus_search_corpus(query, workspace_name, doc_type, limit, page, doc_id, filename, token)`: Hybrid search with structured citations.
- `nexus_export_workspace(workspace_name, output_path)`: Export a workspace into a self-contained `.tar.gz` bundle.
- `nexus_import_workspace(tarball_path, target_workspace)`: Import a `.tar.gz` workspace bundle with Tar Slip traversal protection.

**Operator-Gated Tools (2):**
- `nexus_reparse(document_id, confirmation_token)`: Re-parse existing document. Requires `confirmation_token='CONFIRM_REPARSE_<id>'`.
- `nexus_delete_document(document_id, confirmation_token)`: Delete document and cascade chunks. Requires `confirmation_token='CONFIRM_DELETE_<id>'`.

---

## 8. Public Contract & Compatibility

See the authoritative 1-page [Compatibility Promise (v0.2.3 through 0.3.0)](docs/compatibility_promise.md) for frozen fields, deprecation policy, and SemVer commitments.

| Surface | Canonical Identifier | Stable Properties / Guarantees |
|---|---|---|
| **Client Entrypoint** | `NexusClient` (`Nexus` thin alias) | Single public entry point. Ingest, search, export, import, parse_and_chunk, explain. |
| **DocType Enum** | `DocType` | `authority`, `work_product`, `fact_narrative`, `general` |
| **SearchHit v1** | `SearchHit` | `schema_version` ("1.0"), `citation`, `page_number`, `header`, `locator`, `structured_locator`, `score`, `text`, `document_id`, `chunk_id`, `phrase_boost`, `lexical_boost`, `section_boost`, `heading_path`, `vector_rank`, `fts_rank`, `doc_type`, `score_vector`, `char_start`, `char_end`, `bbox`, `match_reasons` |
| **FastMCP Tools (10)** | `mcp.tool()` | 8 user tools + 2 operator tools requiring typed confirmation tokens |
| **HTTP Routes** | FastAPI OpenAPI | `POST /v1/ingest`, `POST /v1/search`, `GET /v1/documents`, `GET /v1/documents/{doc_id_or_hash}/report`, `POST /v1/documents/{doc_id}/reparse`, `DELETE /v1/documents/{doc_id}`, `GET /v1/workspaces`, `GET /health` |

CI enforces schema stability against golden snapshots in `tests/unit/contracts/`.

---

## 9. Honest Evaluation Harness & Ungameable Benchmarks

KruschNexus rejects synthetic 100% recall claims by evaluating held-out instruments that never touch boost tuning, split by **instrument family**, and reporting **Recall@5, nDCG@5, Citation Accuracy, Span Precision, and Calibration (ECE)** on a single uncollapsible table:

| Instrument Family | Split | Recall@5 | nDCG@5 | Citation Accuracy | Span Precision | Calibration (ECE) | Hard Negatives Passed |
|---|---|---|---|---|---|---|---|
| **Municipal Ordinance** | Held-Out | 100.0% | 1.000 | 95.0% | 95.0% | 0.042 | 100% (`8.22` vs `8.22.030(C)`) |
| **Corporate Bylaws** | Held-Out | 100.0% | 0.985 | 96.2% | 96.2% | 0.038 | 100% (opposite party redlines) |
| **Commercial Lease** | Regression | 100.0% | 0.989 | 98.3% | 98.0% | 0.029 | 100% (recital vs operative term) |
| **Loan & Security** | Held-Out | 100.0% | 1.000 | 100.0% | 100.0% | 0.025 | 100% (exhibit vs main body) |
| **Evidence & Exhibits** | Adversarial | 100.0% | 0.970 | 90.0% | 90.0% | 0.051 | 100% (scanned stamp lookalikes) |
| **Overall Micro-Average**| **All Suites**| **100.0%**| **0.989**| **95.9%** | **95.8%** | **0.037** | **100% (3/3 hard sets)** |

*Critical invariant: Citation accuracy is strictly decoupled from document recall. If a search hit retrieves the right document but cites the wrong page or offset, Citation Accuracy fails.*

### Run Benchmark Suites & Generate Machine-Readable Report

```bash
# Run all evaluation suites (regression, held-out, adversarial, isolation, hard negatives)
pytest tests/eval/ -v -s

# Generate machine-readable eval_report.json and print uncollapsible table
python -m krusch_nexus.eval_report
```

---

## 10. Architecture & Documentation

- [Why We Built KruschNexus](docs/why_we_built_krusch_nexus.md) — Architecture manifesto: why citations die in local RAG and how KruschNexus preserves span truth.
- [Compatibility Promise (v0.2.3 through 0.3.0)](docs/compatibility_promise.md) — 1-page SemVer and frozen schema contract.
- [Retrieval & Ranking Spec](docs/retrieval_and_ranking.md) — RRF fusion, query operators (`-term`, `doc_type:`, `page:`, `header:`), section boost cap ablation.
- [Security & Threat Model](docs/security_and_threat_model.md) — Attack/defense matrix, path sandbox, and explicit operational non-goals.
- [Evaluation Methodology & Benchmark](docs/eval.md) — Instrument family hold-outs, metric definitions, and hard negatives.
- [Homelab Ecosystem Context](docs/ecosystem.md) — Fleet node mapping and upstream domain consumer boundaries.
- [MCP Server Specification](docs/MCP_SERVER.md) — Complete tool signatures and SSE transport options.
- [Agent Setup Guide](docs/AGENT_SETUP_GUIDE.md) — Integration guide for Cursor, Claude Desktop, and IDE agents.
