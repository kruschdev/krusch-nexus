# KruschNexus — Agent Guidelines & Architecture

> **Status**: Production Core (Air-Gapped Corpus Factory)  
> **Version**: 0.2.0  
> **Last updated**: 2026-09-22  

---

## 1. System Architecture

**KruschNexus** is an open-source, local-first document ingestion and citation spine.
- **Backend**: Python 3.11+, FastAPI, SQLAlchemy, Poppler, Tesseract OCR
- **Embeddings**: Local Ollama (`bge-large` 1024d) with persistent disk/DB SHA-256 hash caching
- **Database**: PostgreSQL 16 with `pgvector` (HNSW cosine index) & full-text search (`tsvector` GIN index)
- **Serving**: FastMCP server (`KruschNexusMCP`), Python SDK (`NexusClient`), REST API (`/health`, `/v1/workspaces`, `/v1/ingest`, `/v1/search`)
- **Archival**: Safe watch-folder daemon (`daemon.py`) archiving committed files to `.ingested/` with SHA-256 deduplication

---

## 2. Ingestion Core & Layering Principles

The repository enforces strict separation between the document ingestion core and external consuming applications:

1. **Nexus Core Layout (`src/krusch_nexus/`)**:
   - `parsers.py`: Multi-format parsers (PDF with Poppler/Tesseract OCR fallback, DOCX, EML, CSV, HTML, TXT) with true MIME detection.
   - `chunking.py`: Structure-aware sliding window chunking with provenance, heading stacks, and breadcrumb isolation.
   - `embeddings.py`: Local Ollama embedding client with persistent disk/DB SHA-256 caching.
   - `store.py`: Relational models (`Workspace`, `Document`, `DocumentChunk`, `IngestRun`, `EmbedCache`).
   - `retrieve.py`: 150-line hybrid search (ANN + FTS + RRF k=60 + statutory section boost + quoted phrase boost).
   - `ingest.py`: 8-state single-file pipeline with atomic DB commit, path sandboxing, and post-commit archival.
   - `daemon.py`: Folder-watching daemon with bounded OCR/embed queues and standalone stale-lock reaper.
   - `api.py`: FastAPI REST endpoints (`/v1/ingest`, `/v1/search`, `/v1/documents`, `/v1/workspaces`, `/health`).
   - `mcp.py`: FastMCP server exposing 6 user tools and operator-gated destructive tools.
   - `client.py`: Typed Python SDK (`NexusClient`).
   - `models.py`: Frozen Pydantic schemas (`DocType`, `Citation`, `StructuredLocator`, `SearchHit`, `IngestReport`, `NexusConfig`).
   - `exceptions.py`: Typed error hierarchy.
   - `cli.py`: Unified CLI (`nexus search`, `nexus ingest`, `nexus doctor`, `nexus daemon`, `nexus mcp`).

2. **Domain Separation**:
   - Downstream verticals (e.g., `krusch-law`, `krusch-biz`) interact with Nexus through `NexusClient`, REST API, or MCP tools.
   - Core ingestion logic must never depend on domain-specific legal or business modules, cloud providers, or LLM reasoning.

---

## 3. Testing & Validation

Run unit tests directly with pytest:
```bash
./mcp_env/bin/pytest tests/ -v
```
All tests must execute without network calls to external cloud providers.

---

## 4. 🛡️ Zero-Trust Security & Workspace Isolation Protocol

All AI agents and backend endpoints MUST enforce strict multi-tenant isolation:
1. **Explicit Scoping**: Every document ingestion and search query must be explicitly scoped to an authorized workspace (`Workspace.name` or `workspace_id`). Zero cross-workspace leakage.
2. **Citation Accountability**: Every retrieved context must retain its canonical citation footnote (`[filename, p. X, § Section]`).
3. **No Agent Residue**: Do not commit agent scratchpads, inflight session logs (`*_INFLIGHT.md`), or machine-specific absolute paths to git tracking.
