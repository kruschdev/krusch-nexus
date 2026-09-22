# Changelog

All notable changes to the KruschNexus project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
