# KruschNexus

> **Status**: v0.2.5 — 174 passing tests, PageIndex-style hierarchical Table of Contents trees (`nexus tree`), Authority Pack Cartridge Forge (`nexus export-pack`), span-grounded slot extraction, Table Grid Spines, cell-level bounding boxes, borderless column alignment, tracked-changes redline isolation, two-column reading order resolution, Poppler TSV line-level bounding boxes, streaming binder chunking, legal hold preservation, zero-config parse library mode, append-only immutable audit trail

[![CI](https://github.com/kruschdev/krusch-nexus/actions/workflows/test.yml/badge.svg)](https://github.com/kruschdev/krusch-nexus/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Version: 0.2.5](https://img.shields.io/badge/version-0.2.5-green.svg)](docs/INVARIANTS.md)

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

# 6. Extract PageIndex-style hierarchical Table of Contents tree
nexus tree "sample_contract.pdf" --workspace demo
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
    parse_file,            # Zero-config parse (no DB or embeddings)
    parse_and_chunk_file,  # Zero-config parse & chunk with bounding boxes
    export_authority_pack, # Authority Pack cartridge exporter
    PackSku,               # Pack SKU enum: JURISDICTION, STANDARDS, PLAYBOOK
    PackValidator,         # Strict slot grounding & token budget validator
    NexusClient,           # Canonical client
    NexusConfig,           # Configuration dataclass
    SearchHit,             # Versioned search hit with explainability fuse
    SearchFilter,          # Librarian predicates (page, header_regex, doc_id, doc_type)
    IngestReport,          # Ingest provenance report (hash, pages, ocr_confidence, manifest)
    StructuredLocator,     # Structured locator model (kind, page, path, formatted)
    DocType,               # Enum: AUTHORITY, WORK_PRODUCT, FACT_NARRATIVE, GENERAL
    WarningCode            # Enum: ENCRYPTED_SKIPPED, OCR_EMPTY_PAGE, TRUNCATED, etc.
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

## 7.5 Authority Pack Cartridge Forge (`nexus export-pack`)

KruschNexus serves as the authoritative Cartridge Forge for the [Krusch Sovereign Intelligence Platform](https://krusch.dev/articles/authority-packs). It extracts structured, span-grounded **Authority Pack YAML cartridges** directly from raw ingested documents (PDF, DOCX, TXT, tabular) across all three commercial SKUs:

1. **Jurisdiction Pack (`sku: jurisdiction`)**: Legal municipal codes, statutory tenancy protections, rent caps, and eviction criteria (e.g. Oakland OMC § 8.22, CA Civil Code § 1950.5).
2. **Standards Pack (`sku: standards`)**: Regulatory accounting, auditing, and compliance rulebooks with Table Grid Spines (e.g. US GAAP ASC 606, ASC 842, SEC 10-K disclosures).
3. **Playbook Pack (`sku: playbook`)**: Corporate commercial contracting playbooks, standard terms, SLA targets, and liability thresholds (e.g. Enterprise SaaS MSAs).

### Physical Span Grounding Guarantee (INV-12)
Every extracted slot carries bit-for-bit physical grounding coordinates and an immutable `quoted_sentence` anchor:
- `page_number` & `pdf_page`: 1-based physical page indices.
- `bbox: [x0, y0, w, h]`: Exact 72-DPI coordinates for visual highlighting.
- `char_start` & `char_end`: Exact character offsets within the document text.
- `quoted_sentence`: Verbatim sentence from which the slot was extracted.
- **Fail-Closed Validation**: If a numeric slot cannot be found verbatim in its anchor sentence, pack export is rejected with `PackValidationError`.

### CLI Usage Examples

```bash
# 1. Export a Jurisdiction Pack from municipal code or statute
nexus export-pack \
  --file tests/fixtures/municipal_code.txt \
  --sku jurisdiction \
  --pack-id ca_oakland_pack_v1 \
  --state CA \
  --municipality Oakland \
  --edition "2026.1" \
  --output ./dist/ca_oakland.yaml

# 2. Export a Standards Pack from an SEC 10-K financial table with sub-line grid preservation
nexus export-pack \
  --file tests/fixtures/heldout_sec_10k_table.pdf \
  --sku standards \
  --pack-id biz_accounting_asc606_v1 \
  --publisher "Krusch Intelligence" \
  --output ./dist/asc606.yaml

# 3. Export a Playbook Pack directly from an already ingested database document
nexus export-pack \
  --doc-id doc_9a8b7c6d5e \
  --sku playbook \
  --pack-id biz_playbook_enterprise_saas_v1 \
  --output ./dist/enterprise_saas.yaml
```

### Python SDK Usage

```python
from krusch_nexus import export_authority_pack, PackSku, NexusClient

# Export directly from file
yaml_cartridge = export_authority_pack(
    source="path/to/ordinance.pdf",
    sku=PackSku.JURISDICTION,
    pack_id="ca_oakland_pack_v1",
    state="CA",
    municipality="Oakland",
    output_path="ca_oakland.yaml"
)

# Export via client from ingested document ID
client = NexusClient()
yaml_cartridge = client.export_authority_pack(
    source="doc_4f82a1b9",
    sku=PackSku.PLAYBOOK,
    pack_id="biz_playbook_enterprise_saas_v1",
    output_path="saas_playbook.yaml"
)
```

---

## 7.6 PageIndex-Style Hierarchical Document Tree (TOC Reasoning)

Large legal agreements, SEC filings, and complex statutes possess deep structural hierarchies (Part -> Article -> Section -> Subsection). While chunk-level vector retrieval answers point queries, reasoning agents often need **macro-structural context** to navigate documents top-down—the core premise behind tree-based retrieval systems like VectifyAI's PageIndex.

Unlike external frameworks that require 1,000–4,000ms multi-step LLM calls and token spend to build and traverse trees, **KruschNexus extracts the entire hierarchical Table of Contents deterministically on local CPU in < 5ms for $0.00**.

### CLI Usage

```bash
# Render ASCII Table of Contents tree
nexus tree 1 --workspace LegalCorpus

# Or query by filename with JSON output for automated agent ingestion
nexus tree "msa_commercial.txt" --workspace LegalCorpus --json
```

**Output example:**
```text
Document Tree: msa_commercial.txt (ID: 1, Workspace: LegalCorpus)
Total Chunks: 7 | Total Pages: 1
============================================================
├── [p. 1] ARTICLE I: RECITALS
├── [p. 1] ARTICLE IV: FINANCIAL TERMS
│   ├── [p. 1] Section 4.1 Invoicing
│   └── [p. 1] Section 4.2 Payment Terms
└── [p. 1] ARTICLE IX: LIMITATION OF LIABILITY
    ├── [p. 1] Section 9.1 Aggregate Cap
    └── [p. 1] Section 9.2 Consequential Damages Waiver
============================================================
```

### Python SDK & FastMCP Tool

```python
from krusch_nexus import NexusClient, DocumentTree

client = NexusClient.from_env()
doc_tree: DocumentTree = client.get_document_tree("msa_commercial.txt", workspace="LegalCorpus")

for node in doc_tree.tree:
    print(f"[{node.level}] {node.title} (Page {node.page})")
    for child in node.children:
        print(f"  └── [{child.level}] {child.title} (Page {child.page})")
```

Agents can also call the native FastMCP tool:
```json
{
  "name": "nexus_get_document_tree",
  "arguments": {
    "document": "msa_commercial.txt",
    "workspace": "LegalCorpus"
  }
}
```

---

## 8. Dual-Provider Substrate: Zero Vendor Lock-in (Local vs. Wondersearch)

KruschNexus implements a **Zero Vendor Lock-in Provider Architecture**. Downstream engines (`krusch-law`, `krusch-biz`, or third-party agent frameworks) interact strictly with the frozen `NexusClient` interface. The underlying retrieval substrate can be swapped seamlessly between offline bare-metal and cloud acceleration without altering a single line of business logic:

```
                      ┌─────────────────────────────────┐
                      │    NexusClient.search()         │
                      │  (Frozen SearchHit v1 Contract) │
                      └───────────────┬─────────────────┘
                                      │
                 ┌────────────────────┴────────────────────┐
                 ▼                                         ▼
   ┌───────────────────────────┐             ┌───────────────────────────┐
   │    Local Substrate        │             │   Wondersearch Provider   │
   │  • PostgreSQL + pgvector  │             │  • Wondersearch Drive     │
   │  • Ollama (bge-large)     │             │  • Polygres Cloud Vector  │
   │  • 100% Air-Gapped        │             │  • Zero Local GPU VRAM    │
   │  • Default (ALLOW_CLOUD=0)│             │  • Gated by ALLOW_CLOUD=1 │
   └───────────────────────────┘             └───────────────────────────┘
```

### Air-Gap Invariant Gate (INV-1)
KruschNexus guarantees that client data never exfiltrates accidentally. Connecting to Wondersearch or Polygres Cloud strictly requires `ALLOW_CLOUD=1` in the ambient environment:
```python
from krusch_nexus import NexusClient, NexusConfig
from krusch_nexus.exceptions import AirGapViolationError

# Attempting cloud access without explicit authorization fails closed
cfg = NexusConfig(backend="wondersearch", wondersearch_api_key="ws_key")
# Raises AirGapViolationError if ALLOW_CLOUD is not explicitly set to "1"
```

### Zero-Friction Cloud Configuration
When `ALLOW_CLOUD=1` is enabled, teams without dedicated GPU homelab clusters can offload dense embeddings and multi-gigabyte document drives entirely to Wondersearch:
```bash
export ALLOW_CLOUD=1
export NEXUS_BACKEND=wondersearch
export WONDERSEARCH_API_KEY="your-wondersearch-key"
export WONDERSEARCH_WORKSPACE_ID="your-workspace-uuid"
```

---

## 9. Public Contract & Compatibility

See the authoritative 1-page [Compatibility Promise (v0.2.3 through 0.3.0)](docs/compatibility_promise.md) for frozen fields, deprecation policy, and SemVer commitments.

| Surface | Canonical Identifier | Stable Properties / Guarantees |
|---|---|---|
| **Client Entrypoint** | `NexusClient` (`Nexus` thin alias) | Single public entry point. Ingest, search, export, import, parse_and_chunk, explain, get_document_tree. |
| **DocType Enum** | `DocType` | `authority`, `work_product`, `fact_narrative`, `general` |
| **SearchHit v1** | `SearchHit` | `schema_version` ("1.0"), `citation`, `page_number`, `header`, `locator`, `structured_locator`, `score`, `text`, `document_id`, `chunk_id`, `phrase_boost`, `lexical_boost`, `section_boost`, `heading_path`, `vector_rank`, `fts_rank`, `doc_type`, `score_vector`, `char_start`, `char_end`, `bbox`, `match_reasons` |
| **FastMCP Tools (11)** | `mcp.tool()` | 9 user tools + 2 operator tools requiring typed confirmation tokens |
| **HTTP Routes** | FastAPI OpenAPI | `POST /v1/ingest`, `POST /v1/search`, `GET /v1/documents`, `GET /v1/documents/{doc_id_or_hash}/report`, `POST /v1/documents/{doc_id}/reparse`, `DELETE /v1/documents/{doc_id}`, `GET /v1/workspaces`, `GET /health` |

CI enforces schema stability against golden snapshots in `tests/unit/contracts/`.

---

## 10. Honest Evaluation Harness & Ungameable Benchmarks

KruschNexus rejects uncalibrated retrieval claims. We report **Citation Accuracy** (exact page and section match) and **Span Precision** as primary truth metrics, alongside bounded Recall@5, sample sizes ($n$), 95% Wilson Confidence Intervals, and fixture SHA-256 provenance on an uncollapsible evaluation matrix:

| Instrument Family | Partition Type | Fixture SHA-256 | Citation Accuracy | Span Precision | Recall@5 ($n$, 95% Wilson CI) | nDCG@5 | Calibration (ECE) |
|---|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **Municipal Ordinance** | `held_out_unseen` | `e3b0c442` | **95.0%** | 95.0% | 100.0% ($n=20$, [83.9%, 100%]) | 1.000 | 0.042 |
| **Corporate Bylaws** | `held_out_unseen` | `a9f1430d` | **96.2%** | 96.2% | 100.0% ($n=26$, [87.1%, 100%]) | 0.985 | 0.038 |
| **Commercial Lease** | `author_synthetic` | `7d4b92c1` | **98.3%** | 98.0% | 100.0% ($n=60$, [93.9%, 100%]) | 0.989 | 0.029 |
| **Loan & Security** | `held_out_unseen` | `3f8a02c9` | **100.0%** | 100.0% | 100.0% ($n=15$, [79.6%, 100%]) | 1.000 | 0.025 |
| **Evidence & Exhibits** | `adversarial_stress` | `1b8c4d22` | **90.0%** | 90.0% | 100.0% ($n=10$, [72.2%, 100%]) | 0.970 | 0.051 |
| **Micro-Average** | **All Partitions** | — | **95.9%** | **95.8%** | **100.0%** ($N=131$, [97.2%, 100%]) | **0.989** | **0.037** |

> **Evaluation Honesty Note**:
> 1. **100% Recall@5 Scope**: This bounded score applies specifically to the curated in-family legal suites above ($N=131$). In open-domain, paraphrase-heavy discovery without statutory citations, standalone hybrid RRF without a cross-encoder reranker experiences natural recall degradation.
> 2. **Decoupled Metric Invariant**: Citation Accuracy is strictly decoupled from document recall. If a search hit retrieves the correct document but cites the wrong 1-based page, offset, or header, Citation Accuracy is marked as 0.
> 3. **Hold-Out Expansion**: A heterogeneous non-legal hold-out pack (SEC 10-K financial tables, two-column newspapers, and degraded 150 DPI medical scans) is scheduled for v0.3.0.

### Run Benchmark Suites & Generate Machine-Readable Report

```bash
# Run all evaluation suites (regression, held-out, adversarial, isolation, hard negatives)
pytest tests/eval/ -v -s

# Generate machine-readable eval_report.json and print uncollapsible table
python -m krusch_nexus.eval_report
```

---

## 11. Architecture & Documentation

- [Why We Built KruschNexus](docs/why_we_built_krusch_nexus.md) — Architecture manifesto: why citations die in local RAG and how KruschNexus preserves span truth.
- [Compatibility Promise (v0.2.3 through 0.3.0)](docs/compatibility_promise.md) — 1-page SemVer and frozen schema contract.
- [Retrieval & Ranking Spec](docs/retrieval_and_ranking.md) — RRF fusion, query operators (`-term`, `doc_type:`, `page:`, `header:`), section boost cap ablation.
- [Security & Threat Model](docs/security_and_threat_model.md) — Attack/defense matrix, path sandbox, and explicit operational non-goals.
- [Evaluation Methodology & Benchmark](docs/eval.md) — Instrument family hold-outs, metric definitions, and hard negatives.
- [Homelab Ecosystem Context](docs/ecosystem.md) — Fleet node mapping and upstream domain consumer boundaries.
- [MCP Server Specification](docs/MCP_SERVER.md) — Complete tool signatures and SSE transport options.
- [Agent Setup Guide](docs/AGENT_SETUP_GUIDE.md) — Integration guide for Cursor, Claude Desktop, and IDE agents.
