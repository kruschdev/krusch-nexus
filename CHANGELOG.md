# Changelog

All notable changes to the KruschNexus project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-09-22

### Summary
Deep architectural hardening establishing strict API & contract stability, a modular parser registry with single-policy OCR, honest 4-suite evaluation metrics with decoupled citation accuracy, end-to-end structured locators with `heading_path` array column persistence, explainable retrieval traces, crash-safe ingestion resumption, and robust air-gap security boundaries.

### Added
- **Alembic Schema Migration `c3d4e5f6a7b8`**: Added `heading_path` JSON array column on `document_chunks` table and created the `search_traces` table for rank-level explainability without storing document text.
- **Modular Parser Registry (`src/krusch_nexus/parsers/`)**: Replaced monolithic `parsers.py` with individual modules (`pdf.py`, `ocr.py`, `docx.py`, `eml.py`, `tabular.py`, `html.py`, `registry.py`).
- **Single OCR Policy Dataclass (`OCRPolicy`)**: Centralized `min_printable_chars=40`, `dpi=300`, `psm_prose=6`, `psm_form=4`, `confidence_floor=0.50`, `max_pixels=100_000_000`, `timeout_seconds=30.0`. Preserves `digital_text` and `ocr_text` distinctly.
- **Four Decoupled Evaluation Suites (`tests/eval/`)**:
  - `eval_regression`: 60 frozen fixture queries, Recall@5 = 100.0%, Citation Accuracy = 98.3%, MRR = 0.989.
  - `eval_heldout`: Unseen legal documents, Recall@5 = 100.0%, Citation Accuracy = 100.0%.
  - `eval_adversarial`: Two-column statutes, redlines, empty scans, encrypted PDFs.
  - `eval_isolation`: Multi-tenant cross-workspace property test (75 checks across 5 workspaces, 0.0000% leakage).
- **Comprehensive Evaluation Documentation (`docs/eval.md`)**: Full scoring formulas, query catalog, and explicit benchmark manifest of the 8 OCR testing pages.
- **Crash-Safe Ingestion Resumption**: Persistent `IngestState` transitions in `ingest_runs` (`STAGED`, `PARSED`, `CHUNKED`, `EMBEDDED`, `COMMITTED`, `ARCHIVED`). Property test proves worker kill after `CHUNKED` resumes to `COMMITTED` with exactly 1 document row and 0 duplicate chunks.
- **SQL-Level Predicates & Normalized Section Boosting**: Exact filters for `page`, `doc_id`, `doc_type`, and `filename`. Normalized section matching across citation formats (`§ 1950.5`, `Section 1950.5`, `sec. 1950.5`) with total boost capped at `0.12`.
- **Air-Gap Security & Startup Probe**: Prohibits remote/cloud embedding endpoints (`AirGapViolationError`); enforces mandatory `NEXUS_API_TOKEN` in non-dev; audits localhost interface bindings in `nexus doctor`.
- **Symlink Escape & Image Bomb Defenses**: Sandboxing verifies and rejects symlinks pointing outside allowed roots or to prohibited system directories; image pixel caps prevent decompression bomb attacks.
- **Read-Only Replica Role**: Optional `search_database_url` splits read queries from primary writer database.

### Changed
- **Contract Freezing**: Exported `NexusClient` as canonical SDK client with thin `Nexus = NexusClient` alias. Frozen versioned Pydantic schemas (`SearchHit` v1, `IngestReport` v1). Frozen `DocType` enum (`AUTHORITY`, `WORK_PRODUCT`, `FACT_NARRATIVE`, `GENERAL`).
- **CI Workflow**: Added mypy type audits and an automated 3-line quickstart smoke test against fixtures.

---

## [0.2.0] - 2026-09-22

### Summary
Major architectural refactoring transforming KruschNexus into a dedicated, air-gapped **corpus factory** spine. Removed legacy RAG application cruft (LlamaIndex, LangGraph, graph triplets, OpenRouter, and scratch prototypes), hardened the ingest pipeline into an explicit 8-state machine, introduced Alembic database migrations, eliminated citation inaccuracies (fake `p.1` on DOCX/CSV and breadcrumb vector pollution), and standardized on frozen Pydantic contracts.

### Added
- **Canonical Package Layout**: Unified package structure under `src/krusch_nexus/` with `parsers.py`, `chunking.py`, `embeddings.py`, `store.py`, `retrieve.py`, `ingest.py`, `daemon.py`, `api.py`, `mcp.py`, and `client.py`.
- **Alembic Database Migrations**: Added `alembic.ini` and initial migration `50a73588923a_initial_schema_v020.py` defining `workspaces`, `documents`, `document_chunks`, and `ingest_reports` without runtime DDL.
- **Explicit 8-State Pipeline**: Implemented state machine transitions `DETECTED → STAGED → HASHED → PARSED → CHUNKED → EMBEDDED → COMMITTED → ARCHIVED` (or `FAILED` with sidecar).
- **Single-File Atomic Transactions**: Document metadata, chunks, and ingest reports commit in a single database transaction prior to file renaming/archival.
- **Content-Addressed Archival**: Files archived under `.ingested/<workspace>/<hash[:12]>_<filename>` with metadata including mtime, MIME, parser version, and embedding configuration.
- **Page-at-a-Time PDF Extraction**: PDF parsing executes `pdftotext -f N -l N` per page, inspects encryption via `pdfinfo` with fail-closed checks, and uses 300 DPI Tesseract OCR for image-bearing pages.
- **Provenance-True Citations**: Eliminated fake `p.1` citations for non-paginated files (DOCX, CSV, HTML, EML emit `page_number=None` with structural locators such as heading stacks or row groups).
- **Citation-First Chunking**: Raw document text is isolated for embedding and hashing (no breadcrumb pollution in vectors or hashes); cross-page sliding window overlap supports multi-page legal reasoning.
- **CSV Row-Group Parsing**: Implemented `parse_csv` chunking by row groups with header replay.
- **150-Line Retrieval Engine (`retrieve.py`)**: Deterministic hybrid search combining SHA-256 query embedding cache, pgvector cosine distance, PostgreSQL `tsvector @@ plainto_tsquery`, Reciprocal Rank Fusion ($k=60$), and statutory section boosts.
- **Day-Two Operator APIs**: Added `reparse(document_id)` and `delete_document(document_id)` across REST API, FastMCP server, and Python client SDK.
- **Bearer Token Authentication**: Added optional `NEXUS_API_TOKEN` bearer authentication on API endpoints.
- **Frozen 25-Query Evaluation Harness (`tests/eval/`)**: Benchmark suite asserting Recall@5, Header Accuracy, OCR Recall, and 0.00% cross-workspace leakage.

### Changed
- **Version Number**: Reset from deceptive `1.0.0` to honest `0.2.0` reflecting its corpus factory posture.
- **Dependency Consolidation**: Removed dual `requirements.txt` files and external LlamaIndex/sentence-transformers dependencies from the core package; consolidated all packaging in `pyproject.toml`.
- **Localhost Bindings**: All Docker Compose services and daemon servers bind strictly to `127.0.0.1` (`8000` for REST, `8002` for FastMCP SSE).
- **Watchdog Queue Separation**: Divided concurrency into separate bounded semaphores for OCR vs Embedding, and added a background reaper for `.part` locks older than 600s.
- **Public Client**: Renamed and standardized on `from krusch_nexus import NexusClient` (retaining `Nexus` alias).

### Removed
- **Legacy Shims & Cruft**: Removed `src/backend/` directory and all legacy shim scripts.
- **37 Prototype Scripts**: Removed outdated scratch scripts, inspect utilities, and HTML mockups.
- **LLM Search Cruft**: Completely removed `rag_engine.py`, LangGraph cascades, and external LLM calls from the retrieval path.
