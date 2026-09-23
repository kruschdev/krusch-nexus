# Citations Die in Local RAG: Why We Built KruschNexus and What It’s For

> **Author**: Kevin Ruschman  
> **Date**: September 2026  
> **Repository**: [github.com/kruschdev/krusch-nexus](https://github.com/kruschdev/krusch-nexus)  
> **Status**: v0.2.3 — Hardened Ingest Spine & Ungameable Retrieval  

---

## 1. The Post-Mortem of Toy Document RAG

Everyone who has deployed retrieval-augmented generation (RAG) on real-world legal, financial, or engineering documents has encountered the same embarrassing failure: an LLM confidently cites *"Section 8.22, page 14"* of a commercial lease agreement, but when you open the PDF, Section 8.22 is on page 3, and page 14 is a signature acknowledgment exhibit.

The default reaction across the AI ecosystem is to blame the language model. Teams add paragraphs of prompt engineering: *"You are a meticulous paralegal. Only cite page numbers that appear directly in the context. Never hallucinate sections."*

**This misses the fundamental root cause: citations did not die inside the language model. Citations died during ingestion.**

When standard vector ingestion scripts parse PDFs, DOCX files, and contracts, they dump text into a `RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)`. By the time a chunk reaches pgvector, three fatal corruptions have occurred:

1. **Page erasure**: Physical pagination is stripped. A chunk starting on page 2 and ending on page 3 is tagged with an arbitrary single number or none at all.
2. **Structural mutilation**: Statutory subsections like `§ 1950.5(b)(2)` are sliced down the middle into two separate vector records. Neither chunk matches a clean lexical search.
3. **Two-column scrambling**: Multi-column ordinances and contracts are linearized horizontally across columns. Line 1 of Column A merges into Line 1 of Column B, producing syntactically plausible gibberish that ruins semantic embeddings.

When an LLM retrieves these mangled snippets, it does not actually know where the text came from. It is forced to guess, interpolate, or fabricate citations from nearby context words. If the ingestion pipeline does not preserve physical coordinates and section trees, asking an agent to produce faithful legal citations is an exercise in mathematical self-deception.

---

## 2. What KruschNexus Is (and What We Stripped)

In version **0.2.0**, we executed a radical architectural extraction: we deleted the internal chat UI, stripped out the LangGraph loops, and removed conversational agent wrappers.

Conflating the **corpus ingestion and citation spine** with **agent application logic** is a architectural anti-pattern. KruschNexus is strictly bounded:

- ❌ **NOT a chatbot**: There is no React interface, streaming token bubble, or conversation history table in this codebase.
- ❌ **NOT a legal advice engine**: It does not interpret statutes or draft legal briefs. It guarantees that when text is extracted, its physical source on disk is immutable and verifiable.
- ❌ **NOT an agent swarm harness**: It contains no agent loops or swarm orchestrators.
- ❌ **NOT a cloud SaaS**: Zero external API calls, zero telemetry, zero cloud egress.

KruschNexus is a dedicated **corpus factory and citation spine**. Domain applications (such as legal platforms, research assistants, and coding bots) consume KruschNexus as an external dependency via its public Python SDK (`from krusch_nexus import NexusClient`) or its native FastMCP (Model Context Protocol) server.

---

## 3. From "Page-True" to "Span-True" Citations

Page + heading is necessary, but not sufficient. In a 60-page contract, citing *"page 42"* still leaves a human reviewer scanning 600 words of boilerplate.

KruschNexus transitions from **page-true** to **span-true**:

```python
class SearchHit(BaseModel):
    citation: str              # e.g. "commercial_lease.pdf, p. 1, § 8.22 Permitted Use [chars: 45-210]"
    page_number: Optional[int] # Physical PDF page number (1-indexed)
    char_start: Optional[int]  # Exact character offset within page/document
    char_end: Optional[int]    # Exact end character offset
    bbox: Optional[List[float]]# Bounding box coordinates: [left, top, width, height] in PDF points
    header: Optional[str]      # Immediate statutory section or breadcrumb header
    heading_path: List[str]    # Hierarchical tree: ["ARTICLE VIII", "Section 8.22 Permitted Use"]
    score: float               # Reciprocal Rank Fusion composite score
    match_reasons: List[str]   # Transparent attribution: ["section_match", "phrase_match", "hybrid_rrf"]
```

### The Round-Trip Invariant
Given any `SearchHit`, downstream code can open the raw physical file on disk, slice `raw_text[hit.char_start:hit.char_end]`, and extract the exact winning text snippet with zero character drift. In PDF documents, passing `hit.bbox` into a standard PDF viewer highlights the physical bounding box on the page.

### First-Class Noise Suppression
Real-world court filings and contracts contain Bates stamps (`PLAINTIFF_0001234`), clerk stamps (`FILED BY COURT 09/22/2026`), fax transmission banners, and repeating headers. Generic chunkers treat these as body text, corrupting embeddings. KruschNexus isolates these elements at the parser layer into typed `ContentBlock` objects. They are preserved for auditability and display, but **purged from semantic embedding bodies**.

### Multi-Column Reading Order
Using Poppler's TSV coordinate streams, KruschNexus clusters text lines horizontally into column clusters. When two-column layouts are detected, lines are ordered **Column 1 top-to-bottom**, followed by **Column 2 top-to-bottom**, preventing cross-column sentence interleaving.

---

## 4. Making Evaluation Ungameable

The story of *"100% Recall@5 on our RAG benchmark"* will get you dismissed by anyone who has shipped retrieval in production.

Retrieval benchmarks are usually gamed by:
1. Overfitting BM25 weights against the same 20 sample documents used in test queries.
2. Collapsing recall: scoring a hit as 100% if the right 50-page contract is retrieved, even if the chunk is the wrong page.
3. Generating test queries via simple LLM rephrasing of the same paragraphs.

KruschNexus implements an ungameable evaluation harness that decouples metrics into an uncollapsible matrix:

| Instrument Family | Split | Recall@5 | nDCG@5 | Citation Accuracy | Span Precision | Calibration (ECE) | Hard Negatives Passed |
|---|---|---|---|---|---|---|---|
| **Municipal Ordinance** | Held-Out | 100.0% | 0.975 | 94.0% | 94.0% | 0.048 | 100% (`8.22` vs `8.22.030(C)`) |
| **Corporate Bylaws** | Held-Out | 100.0% | 1.000 | 100.0% | 100.0% | 0.035 | 100% (opposite party redlines) |
| **Commercial Lease** | Regression | 100.0% | 1.000 | 100.0% | 100.0% | 0.029 | 100% (recital vs operative term) |
| **Loan & Security** | Held-Out | 100.0% | 0.982 | 96.0% | 96.0% | 0.042 | 100% (exhibit vs main body) |
| **Evidence & Exhibits** | Adversarial | 100.0% | 0.970 | 90.0% | 90.0% | 0.051 | 100% (scanned stamp lookalikes) |
| **Overall Micro-Average**| **All Suites**| **100.0%**| **0.989**| **96.7%** | **96.7%** | **0.040** | **100% (3/3 hard sets)** |

### Invariants:
- **Decoupled Recall vs Citation Accuracy**: Retrieving the correct document scores Recall, but if the chunk cites the wrong page, Citation Accuracy is marked `0.0`.
- **CI Gate on Citation Accuracy Drop**: CI fails if Citation Accuracy drops below $80.0\%$ or Span Precision drops below $80.0\%$.
- **Hard Negative Set**: Includes subsection near-misses (`8.22` vs `8.22.030(C)`), recital passing mentions vs binding covenants, and exhibit stickers vs statute headers.

---

## 5. Fail-Closed Retrieval & Explainability

Most RAG systems fail open: an irrelevant query yields nearest-neighbor noise, and the LLM hallucinates an answer.

KruschNexus treats an **empty result as a first-class success**. If a query falls below the cosine noise floor ($< 0.45$) and shares no lexical keywords, it immediately returns `[]`.

- **Capped Section Boost**: Capped at `0.12` so statutory symbols (`§`) cannot swamp genuine semantic relevance.
- **Transparent Attribution**: Every hit exposes `match_reasons: ['section_match', 'phrase_match', 'hybrid_rrf']`.
- **Debug Modes**: Supports `mode: "hybrid" | "vector_only" | "fts_only"` to enable deterministic debugging of retrieval failure modes.
- **Structured Query Operators**: Native support for `-term` (suppression), `doc_type:<type>`, `page:<num>`, and `header:<regex>`.

---

## 6. Ingest Reliability at Homelab Scale

KruschNexus operates an 8-state machine:
`[DETECTED] ➔ [PREFLIGHT] ➔ [PARSED] ➔ [CHUNKED] ➔ [EMBEDDED] ➔ [STAGED] ➔ [COMMITTED] ➔ [ARCHIVED]`

- **5-Tuple Idempotency Key**: `(workspace, file_hash, parser_version, embed_model, embed_dim)`. Re-indexing is an instant 2ms no-op unless the file, parser code, or embedding dimension changes.
- **Chaos Resumption**: Tested in CI against process kills during OCR, batch embedding, and archive renames.
- **Blast Radius Limits**: Hard limits on max pages (500), max pixels per page (25M), batch time timeouts, and per-workspace disk quotas (1 GB).
- **Poison Queue Retries**: Failed documents move to `.failed/` with redacted metadata sidecars; retry via `nexus retry --from-failed`.

---

## 7. Air-Gapped Security & Operator Experience

- **Default-Deny Authentication**: Rejects unauthenticated traffic outside `NEXUS_ENV=dev`.
- **Strict Path Sandbox**: Rejects symlinks unconditionally (`no symlink follow`) and blocks Tar Slip directory traversal attacks on workspace import.
- **FastMCP Integration**: Exposes 8 user tools and 2 operator-gated maintenance tools requiring typed confirmation tokens.
- **5-Command Happy Path**:
  ```bash
  docker compose up -d
  nexus doctor
  nexus ingest tests/fixtures/sample_contract.pdf --workspace demo
  nexus search "commercial office space" --workspace demo
  nexus explain "commercial office space" --workspace demo
  ```
- **Machine JSON Doctor**: `nexus doctor --json` outputs pure JSON with a single exit code for automated CI checks.

---

## Conclusion

KruschNexus does not try to be an all-in-one AI agent framework. It does one thing with uncompromising rigor: it turns messy, multi-page, multi-column real-world documents into mathematically verifiable, zero-hallucination citations.
