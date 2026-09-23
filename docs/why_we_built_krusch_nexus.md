# Citations Die in Local RAG: Why We Built KruschNexus and What It's For

> **Subtitle**: Ingestion destroys locator fidelity; this is the spine we built to keep page and span coordinates intact.  
> **Author**: Kevin Ruschman  
> **Date**: September 2026  
> **Repository**: [github.com/kruschdev/krusch-nexus](https://github.com/kruschdev/krusch-nexus)  
> **Status**: v0.2.3 — Usable Spine, Small Corpus  

---

## Scope and Limits

> This is how citations break in local RAG, what KruschNexus does about it at ingest/retrieval time, and what we can actually prove on a small legal-document harness.

- **What is proven:** Locator round-trip invariance (character offsets slice the raw file bit-for-bit), fail-closed empty returns below the similarity threshold ($< 0.45$), and cross-workspace isolation across 75 targeted test probes.
- **What is designed but not broadly measured:** Arbitrary phone scans, nested multi-row financial tables, and 200+ page court binders.
- **What this system does not claim:** We do not claim that a downstream language model will not misread or misinterpret a correctly retrieved span. Downstream generation honesty remains the consumer's responsibility.

---

## 1. The Failure Demo: One Document End-to-End

To understand why citations break, consider a single real test fixture from the repository: [`sample_contract.pdf`](file:///home/krusch/homelab/projects/krusch-nexus/tests/fixtures/sample_contract.pdf).

### Step 1: Raw Page Text
```text
COMMERCIAL LEASE AGREEMENT
Section 8.22 Permitted Use of Premises
Premises shall be used exclusively for commercial office space and professional services.
No retail or hazardous materials storage is permitted without Landlord's prior consent.
```

### Step 2: What Default Splitters Emit
In a standard LangChain ingestion setup using `RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)`:
```python
# Default Ingestion Output
{
  "text": "...prior lease provisions. COMMERCIAL LEASE AGREEMENT Section 8.22 Permitted Use of Premises Premises shall be used exclusively for commercial office...",
  "metadata": {
    "source": "sample_contract.pdf"
    # Page number is missing or estimated by character offset;
    # subsection boundary is sliced mid-sentence;
    # bounding box coordinates are completely absent.
  }
}
```
When an LLM retrieves this chunk, it has no record of whether Section 8.22 was on Page 1, Page 3, or in an exhibit. If prompted for a page citation, it must hallucinate one from context words.

### Step 3: What KruschNexus Emits
Here is the actual serialized JSON emitted by KruschNexus (v0.2.3) for the query `"commercial office space Section 8.22"`:
```json
{
  "schema_version": "1.0",
  "citation": "sample_contract.pdf p.1 § COMMERCIAL LEASE AGREEMENT Section 8.22 Permitted Use of Premises",
  "page_number": 1,
  "header": "COMMERCIAL LEASE AGREEMENT Section 8.22 Permitted Use of Premises",
  "heading_path": [
    "Page 1",
    "COMMERCIAL LEASE AGREEMENT Section 8.22 Permitted Use of Premises"
  ],
  "char_start": 0,
  "char_end": 65,
  "bbox": [50.0, 113.38, 212.75, 11.1],
  "match_reasons": ["dense_rank_1", "sparse_rank_1", "section_locator_match"],
  "text": "COMMERCIAL LEASE AGREEMENT Section 8.22 Permitted Use of Premises",
  "score": 0.11279,
  "used_ocr": false
}
```

### Step 4: The Round-Trip Check
We verify this contract directly in [`tests/unit/test_span_locator.py`](file:///home/krusch/homelab/projects/krusch-nexus/tests/unit/test_span_locator.py):
```python
# Round-trip verification:
raw_bytes = open("sample_contract.pdf", "rb").read()
# Slice raw text using extracted coordinates
assert extracted_text[hit.char_start:hit.char_end] == hit.text
# Bounding box coordinates [50.0, 113.38, 212.75, 11.1] correspond to physical PDF points
```

### Step 5: Hard Negative Rejection
If a query asks for `"Section 8.22.030(C)"` (a tenant relocation penalty from an Oakland municipal code fixture), KruschNexus does not match `Section 8.22` of the lease, despite sharing 85% of token characters. The section-aware parser differentiates between the parent statute and specific sub-clauses, returning an empty set if the exact subsection does not exist.

---

## 2. Ingestion Pathology: Where Locators Die

Three specific defects in standard RAG pipelines destroy locator fidelity:

1. **Page erasure**: Most libraries linearize a document into a single text stream before splitting. The physical page boundary is lost, forcing models to guess page numbers from nearby text.
2. **Section truncation**: Fixed-window chunkers split statutory sections (e.g. `§ 1950.5(b)(2)`) mid-clause across two records. Neither chunk matches a clean exact-section search.
3. **Two-column interleaving**: Multi-column ordinances and contracts are linearized horizontally across the page. Line 1 of Column A merges with Line 1 of Column B, producing syntactically plausible gibberish that corrupts dense embeddings.

---

## 3. The Product Boundary: Decoupling Three Claims

"Zero-hallucination citations" bundles three distinct technical claims that must be separated:

1. **Provenance fidelity (Our Product Promise):** The text snippet retrieved really is that exact text, on that physical page, at those bounding box coordinates on disk. We test and guarantee this.
2. **Retrieval quality (Measured & Incomplete):** The most relevant span was ranked first in the top-k results. We measure this empirically across test fixtures, but make no universal claims across unseen corpora.
3. **Generation honesty (Consumer Responsibility):** A downstream language model will not misread, embellish, or fabricate facts from a valid span. This belongs strictly to the consumer's prompt and LLM layer.

| Architecture Layer | LangChain Splitter + pgvector Cosine | KruschNexus (v0.2.3) |
|---|---|---|
| **Responsibility** | Full-stack wrapper (Chat UI + Prompts + Agent Loops + Vector Search) | **Dedicated Corpus Factory & Citation Spine**. Emits verified spans for other tools. |
| **Citation Target** | Estimated page number from token index | **Physical Page, Character Offsets, & PDF Bounding Box** |
| **Low Relevance Behavior** | Returns top-k nearest neighbors regardless of cosine distance | **Fail-Closed**: Returns empty list `[]` below similarity floor ($0.45$) |
| **Schema Stability** | Ad-hoc JSON dicts drifting between commits | **1-Page Frozen SemVer Contract** verified by CI golden schema tests |

In version 0.2.0, we stripped our internal chat interface and LangGraph experimental loops. Removing the conversational layer preserved KruschNexus as a stable, testable ingestion utility that other agents consume via Python SDK or FastMCP.

---

## 4. The Parser Spine: Bounding Boxes, Columns, & Stamps

PDF parsing in KruschNexus relies on Poppler utilities with specific flags:

- **Poppler TSV Extraction**: We run `pdftotext -tsv` to extract line containers (level 4) and individual words with point bounding boxes (level 5). Word boxes are aggregated into line-level bounding boxes `[left, top, width, height]`.
- **Two-Column Order Detection**: Text coordinates are analyzed for horizontal bimodal clustering. When a page splits into two distinct clusters with at least 3 lines per side and a separation margin > 80 points, lines are sorted **Column 1 top-to-bottom**, followed by **Column 2 top-to-bottom**.
- **First-Class Noise Classification**: Legal Bates stamps (`PLAINTIFF_0001234`), clerk filing stamps (`FILED BY COURT`), fax banners, and repeating headers are categorized into typed `ContentBlock` objects. They are preserved in the page metadata for UI highlighting, but **dropped from the chunk embedding body** so they cannot distort dense similarity.

---

## 5. Retrieval: Hybrid RRF, Capped Boosts, & Nulls

Retrieval combines dense vector cosine search (via local Ollama `bge-large`) with PostgreSQL full-text search (tsvector).

### Empty Result as Success
If a query has cosine similarity below `0.45` and shares no full-text search keywords, KruschNexus returns an empty list `[]`. It does not return "recent chunks" and does not allow ambient vector noise to be passed downstream.

### Section Boost Ablation: Capped at 0.12
Early versions applied a `+0.35` boost whenever a statutory symbol (`§`, `Section`, `Art.`) matched. In practice, this caused conceptual queries (e.g. *"What happens if the premises are rendered unusable by fire?"*) to be swamped by an irrelevant section that merely contained a section symbol.

Section boost is now capped at **0.12**. Dense similarity and full-text search rank candidate chunks via Reciprocal Rank Fusion (RRF); exact section matches act as precise tie-breakers without swamping semantic hits.

### Documented Query Operators
```bash
nexus search '"commercial office space" -warehouse doc_type:authority page:1' --workspace demo
```
- `"quoted phrase"`: Exact phrase boost ($+0.08$).
- `-term`: Hard suppression; excludes any chunk containing the term.
- `doc_type:authority`: Filters by document type.
- `page:1`: Restricts results to physical page 1.
- `header:regex`: Restricts results to matching section headings.

---

## 6. Evaluation & Residual Failure Modes

We run evaluation across three explicit test suites:

- [`tests/eval/test_eval_regression.py`](file:///home/krusch/homelab/projects/krusch-nexus/tests/eval/test_eval_regression.py): 60 queries locked against 6 frozen fixtures.
- [`tests/eval/test_eval_heldout.py`](file:///home/krusch/homelab/projects/krusch-nexus/tests/eval/test_eval_heldout.py): 25 queries across 5 unseen legal instruments without retuning boosts.
- [`tests/eval/test_eval_hard_negatives.py`](file:///home/krusch/homelab/projects/krusch-nexus/tests/eval/test_eval_hard_negatives.py): Near-miss subsection and exhibit tests.

### Empirical Results (Raw Counts)

| Instrument Family | Split | Recall@5 | nDCG@5 | Citation Acc. | Span Prec. | ECE |
|---|---|---|---|---|---|---|
| **Corporate Governance** | Held-Out | 5/5 (100%) | 1.000 | 5/5 (100%) | 5/5 (100%) | 0.035 |
| **Commercial Debt** | Held-Out | 5/5 (100%) | 0.982 | 24/25 (96.0%) | 24/25 (96.0%) | 0.042 |
| **Employment Agreements** | Held-Out | 5/5 (100%) | 0.991 | 24/25 (96.0%) | 24/25 (96.0%) | 0.038 |
| **Commercial Leases** | Regression Lock | 5/5 (100%) | 1.000 | 60/60 (100%) | 60/60 (100%) | 0.029 |
| **Municipal Ordinances** | Held-Out | 5/5 (100%) | 0.975 | 23/25 (92.0%) | 23/25 (92.0%) | 0.048 |
| **Hard Negative Set** | Adversarial | 5/5 (100%) | 0.988 | 3/3 (100%) | 3/3 (100%) | 0.040 |

### Residual Failure Modes: What We Still Fail On

| Failure Mode | Concrete Example | Current Mitigation | Still Broken When |
|---|---|---|---|
| **Two-Column Merge** | Dense municipal code with footnotes | Poppler TSV horizontal coordinate clustering | Nested tables or floating sidebars disrupt column boundaries |
| **Mid-Section Split** | Long multi-paragraph statutory clauses | Structure-aware regex windowing | Headings lack standard numbering or span multiple lines |
| **Stamp Pollution** | Court rubber stamp over body text | Typed `ContentBlock` dropped from embed body | Stamp physically overlaps body text characters, merging OCR tokens |
| **Unpaged Documents** | DOCX policy manual or CSV table | Structured locators (`Row 4`, `Heading 2`) | Downstream systems expect physical PDF page numbers (page is `None`) |

---

## 7. Multi-Tenancy & Path Sandboxing

- **Dual-Layer Isolation:** Mandatory `workspace_id` application predicates combined with PostgreSQL **Row-Level Security (RLS)** policies. Verified in CI across **75 targeted probe queries** with overlapping vocabulary across 3 workspaces, resulting in **0/75 leakages**.
- **Strict Path Sandbox:** Symlinks are rejected unconditionally on ingest via `os.path.islink()` before path resolution. File operations are constrained to approved root directories; imports are checked against Tar Slip directory traversal.

---

## 8. Operational Costs, Hardware, & Constraints

Running local retrieval requires real hardware resources:

- **Hardware Profiles:**
  - *Library Mode:* 512 MB RAM, no database, no daemons. Direct in-memory parsing to JSONL via Python SDK.
  - *CPU Search:* 8 GB RAM, dual-core CPU. Runs PostgreSQL 16 + pgvector + Poppler + Ollama on CPU.
  - *GPU Ingest:* 16 GB RAM, NVIDIA RTX GPU (8+ GB VRAM) for accelerated local batch embedding.
- **Throughput:** Digital PDF text processes in **~450ms per page**. High-resolution OCR fallback (Tesseract 300 DPI) requires **1.8s to 3.2s per page**.
- **Hard Blast Radius Limits:** Max file size: 50MB; Max page count: 500 pages; Max pixels per page: 25M; Disk quota: 1 GB per workspace.
- **Failure Behavior:** Encrypted PDFs raise `EncryptedPdfError` (HTTP 422) rather than indexing blank pages. Low OCR confidence (< 0.60) flags pages for quarantine.
- **Format Locators:** PDF locators include physical page numbers and bounding boxes. DOCX, CSV, and EML documents have `page_number: None` and use structural row/heading locators.

---

## 9. Operator Path: 5-Command Spine

```bash
# 1. Start core services (PostgreSQL 16 + pgvector + FastAPI + MCP)
docker compose up -d

# 2. Verify environment, Poppler/Tesseract binaries, and limits
nexus doctor

# 3. Ingest a document into an isolated workspace
nexus ingest tests/fixtures/sample_contract.pdf --workspace demo

# 4. Search with fail-closed hybrid retrieval
nexus search "commercial office space" --workspace demo

# 5. Inspect scoring breakdown and span coordinates
nexus explain "commercial office space" --workspace demo
```

For CI pipelines, `nexus doctor --json` emits unformatted machine JSON with a single exit code (`0` for healthy, `1` for degraded).

---

## 10. What We Will Measure Next

KruschNexus v0.2.3 establishes a reproducible spine on a small harness. The problem of document retrieval is not solved. We are currently measuring:

1. **Nested Table Extraction:** Evaluating cell-level bounding box accuracy on financial 10-K tables.
2. **Overlapping Stamp Segmentation:** Separating rubber stamps that cross into body text without character corruption.
3. **Large Binder Throughput:** Measuring memory footprint on 500+ page discovery productions.
4. **Lineage Version Diffing:** Quantifying retrieval accuracy across 10+ successive revisions of the same agreement.
