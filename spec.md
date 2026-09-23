# KruschNexus — System Specification

> **Version**: 0.2.3  
> **Status**: Production Core (Air-Gapped Corpus Factory)  
> **Architecture**: Local-First / Air-Gapped Document Ingestion & Page-True Citation Spine  
> **Author**: kruschdev  

---

## 1. Executive Summary

**KruschNexus** is an open-source, local-first document ingestion and corpus indexing engine. It solves the foundational failure point of local retrieval systems: transforming heterogeneous, unstructured files (PDFs with selectable and scanned text, Word documents, email archives, spreadsheets, HTML, markdown) into structure-aware, citation-preserving vector embeddings with exact 1-based page numbers and section headers.

KruschNexus operates strictly as an air-gapped **corpus factory**, not an agentic RAG application. Downstream applications (such as KruschLaw, KruschBiz, or IDE agents) consume chunks, citations, and search hits through thin adapters (FastMCP, Python SDK, or REST API). Graph extraction, multi-agent swarms, and privilege reasoning belong in domain verticals, not the corpus spine.

---

## 2. Core Invariants

1. **Air-Gapped & Local-First by Default**:
   All core parsing, OCR fallback, structural chunking, embedding generation, and retrieval operate 100% locally without external cloud dependencies.
   - Parsing: Native XML for DOCX, native RFC822 for EML with MIME decoding, Poppler page-at-a-time `pdftotext -f N -l N -layout` for PDF with `pdfinfo` encrypted check.
   - OCR Fallback: Local Tesseract (`tesseract-ocr` via `pdftoppm -r 300`) with `--psm 6` for prose and `--psm 4` for sparse legal forms, language allowlists, and image XObject verification.
   - Embeddings: Local Ollama HTTP client (`bge-large`, 1024-dim) with persistent disk/DB SHA-256 text hash caching.
   - Database: PostgreSQL with `pgvector` (HNSW cosine index) and full-text search (`tsvector` GIN index). Alembic migrations manage schema evolutions without runtime DDL.

2. **Citation-First Provenance**:
   - Page-true citations (`p.N`) are emitted only for formats where native page breaks exist (PDF). DOCX, HTML, EML, and CSV emit `page_number=None` and use structural locators (e.g. `policy.docx § Section 1.2 > Backup Retention Standards` or `matrix.csv § Rows 1-4`).
   - Breadcrumb strings (`[lease.pdf - p.14] § 1950.5`) are NEVER concatenated into embedded text or chunk hash inputs. Raw text is embedded and hashed independently; citations are rendered in dedicated metadata fields.
   - Cross-page sliding window overlap preserves context across physical page boundaries without polluting heading boundaries.

3. **Hardened 8-State Ingestion Pipeline**:
   The ingest lifecycle progresses through explicit states:
   `DETECTED → STAGED → HASHED → PARSED → CHUNKED → EMBEDDED → COMMITTED → ARCHIVED` (or `FAILED` with sidecar).
   - Atomic Transactions: The document metadata, chunks, and ingest report are committed to Postgres in a single transaction before the file is archived.
   - Content-Addressed Archival: Archived files are stored under `.ingested/<workspace>/<hash[:12]>_<filename>`.
   - Bounded Queues & Stale-Lock Reaper: Separate concurrency semaphores for OCR vs Embed paths; background reaper removes orphaned `.part` locks older than 600s.
   - Typed Failures: Errors raise explicit exceptions (`TooLargeError`, `EncryptedPdfError`, `EmptyOcrError`, `UnsupportedMimeError`, `CorruptedFileError`) and generate redacted sidecars.

4. **150-Line Retrieval Spine**:
   Search executes a deterministic, LLM-free hybrid pipeline:
   1. Compute query hash & check query embedding cache.
   2. Vector ANN in tenant workspace via pgvector cosine distance (`<=>`).
   3. Lexical full-text search in tenant workspace via `tsvector @@ plainto_tsquery`.
   4. Reciprocal Rank Fusion (RRF, $k=60$) merging dense and sparse ranks.
   5. Exact quote phrase boosting (`"liquidated damages"`).
   6. Statutory section boost if query contains section symbols or markers (`§`, `Section N`, `Art. IV`).
   7. Return typed `SearchHit` models with exact citations and explainability fuse.

5. **Tenant Isolation & Security**:
   - Localhost-only binding (`127.0.0.1:8000`, `127.0.0.1:8002`).
   - Constant-time Bearer token authentication via `NEXUS_API_TOKEN`.
   - Mandatory workspace tenant key on every query (`WHERE workspace_id = :ws_id`); cross-workspace leakage is strictly 0.00%.
   - Zero document text in operational logs.

---

## 3. Package Structure

```text
src/krusch_nexus/
  ├── parsers.py         # Multi-format parsers (PDF, DOCX, EML, CSV, HTML, TXT) with MIME detection
  ├── chunking.py        # Structure-aware sliding window chunking with provenance and breadcrumb isolation
  ├── embeddings.py     # Local Ollama embedding client with persistent disk/DB SHA-256 caching
  ├── store.py           # SQLAlchemy relational models (workspaces, documents, chunks, ingest_runs, embed_cache)
  ├── retrieve.py        # 150-line hybrid search (ANN + FTS + RRF k=60 + section boost + phrase boost)
  ├── ingest.py          # 8-state single-file pipeline with atomic DB commit and path sandboxing
  ├── daemon.py          # Watchdog folder daemon with bounded OCR/embed & standalone stale-lock reaper
  ├── api.py             # FastAPI REST endpoints (/v1/ingest, /v1/search, /v1/documents, /health)
  ├── mcp.py             # FastMCP server exposing 6 user tools and operator-gated destructive tools
  ├── client.py          # Typed Python SDK (NexusClient)
  ├── models.py          # Frozen Pydantic schemas (DocType, Citation, StructuredLocator, SearchHit, IngestReport, NexusConfig)
  ├── exceptions.py      # Typed error hierarchy
  └── cli.py             # Unified CLI (nexus search, ingest, doctor, daemon, mcp)
```

---

## 4. Frozen Data Contracts

```python
class DocType(str, Enum):
    AUTHORITY = "authority"
    WORK_PRODUCT = "work_product"
    FACT_NARRATIVE = "fact_narrative"
    GENERAL = "general"

class IngestReport(BaseModel):
    document_id: Optional[int]
    filename: str
    file_hash: str
    workspace: str
    doc_type: DocType
    parser_name: str
    parser_version: str
    detected_mime: str
    total_pages: int
    total_chunks: int
    ocr_pages: List[int]
    ocr_confidence: Dict[int, float]
    ocr_mean_confidence: Optional[float]
    duration_ms: float
    warnings: List[str] = Field(default_factory=list)
    status: str = "completed"

class SearchHit(BaseModel):
    chunk_id: int
    document_id: int
    filename: str
    workspace: str
    citation: str
    page_number: Optional[int]
    header: Optional[str]
    locator: Optional[str]
    structured_locator: Optional[StructuredLocator]
    score: float
    text: str
    source_hash: str
    doc_type: Optional[str] = None
    vector_rank: Optional[int] = None
    fts_rank: Optional[int] = None
    section_boost: bool = False
    phrase_boost: bool = False
    match_reasons: List[str] = Field(default_factory=list)
```

---

## 5. Serving Interfaces & Tool Catalog

- **FastMCP Server**:
  User Tools:
  1. `nexus_list_workspaces`
  2. `nexus_list_documents`
  3. `nexus_ingest_file`
  4. `nexus_ingest_directory`
  5. `nexus_get_ingest_report`
  6. `nexus_search_corpus`
  Operator-Restricted Tools (`operator_confirmed=True` required):
  7. `nexus_reparse`
  8. `nexus_delete_document`

- **NexusClient SDK**:
  ```python
  from krusch_nexus import NexusClient, DocType

  client = NexusClient.from_env()
  report = client.ingest("contract.pdf", workspace="litigation", doc_type=DocType.AUTHORITY)
  hits = client.search('"liquidated damages" Section 14.1', workspace="litigation", limit=5)
  for hit in hits:
      print(f"{hit.citation} (score: {hit.score:.3f}): {hit.text[:100]}...")
  ```

---

## 6. Deployment & Migrations

- **Database Migrations**:
  ```bash
  alembic upgrade head
  ```
- **Environment & Binaries Audit**:
  ```bash
  nexus doctor
  ```
- **Docker Compose**:
  All published ports bind strictly to `127.0.0.1`.
  ```bash
  docker compose up -d
  ```
  Services:
  1. `db`: PostgreSQL 16 with `pgvector` extension.
  2. `backend`: FastAPI server on `127.0.0.1:8000`.
  3. `ingest-worker`: Background watch-folder daemon with 4GB memory cap.
  4. `mcp-server`: FastMCP SSE server on `127.0.0.1:8002`.
