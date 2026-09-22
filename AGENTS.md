# KruschNexus — Agent Guidelines & Architecture

> **Status**: Production Core (v1.0.0)  
> **Last updated**: 2026-09-22  

---

## 1. System Architecture

**KruschNexus** is an open-source, local-first document ingestion and hybrid RAG engine.
- **Backend**: Python 3.11+, FastAPI, SQLAlchemy, Poppler, Tesseract OCR
- **Embeddings**: Local Ollama (`bge-large` 1024d) with in-memory SHA-256 hash caching
- **Database**: PostgreSQL 16 with `pgvector` (HNSW cosine index) & full-text search (`tsvector` GIN index)
- **Serving**: FastMCP server (`KruschNexusMCP`), Python client (`NexusIngestClient`), REST API (`/health`, `/api/workspaces`, `/api/upload`, `/api/search/quick`)
- **Archival**: Safe watch-folder daemon (`ingest_daemon.py`) archiving processed files to `.ingested/` with SHA-256 deduplication

---

## 2. Ingestion Core & Layering Principles

The repository enforces strict separation between the document ingestion core and external consuming applications:

1. **Nexus Core Modules**:
   - `parsers.py`: PDF (with OCR fallback), DOCX, EML, and plaintext parsers.
   - `chunking.py`: Structural boundary chunker (§, headings, token budgets, sliding-window overlap, SHA-256 hashing).
   - `embeddings.py`: Local Ollama embedding client with caching.
   - `db.py`: Database models (`Workspace`, `Document`, `DocumentChunk`, `IngestReport`).
   - `rag_engine.py`: Hybrid dense vector + full-text RRF search engine.
   - `ingest_daemon.py`: Directory scanner and safe `.ingested/` archiver.
   - `client.py`: Clean programmatic client (`NexusIngestClient`).
   - `mcp_server.py`: FastMCP tools for AI agent integrations.

2. **Domain Separation**:
   - External verticals (e.g., `krusch-law`, `krusch-biz`) interact with Nexus through `NexusIngestClient` or MCP tools.
   - Core ingestion logic must never depend on domain-specific legal or business modules.

---

## 3. Testing & Validation

Run unit tests directly with standard unittest or pytest:
```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```
All tests must execute without network calls to external cloud providers.

---

## 4. 🛡️ Zero-Trust Security & Workspace Isolation Protocol

All AI agents and backend endpoints MUST enforce strict multi-tenant isolation:
1. **Explicit Scoping**: Every document ingestion and search query must be explicitly scoped to an authorized workspace (`Workspace.name` or `workspace_id`).
2. **Citation Accountability**: Every retrieved context must retain its canonical citation footnote (`[filename, p. X, § Section]`).
3. **No Agent Residue**: Do not commit agent scratchpads, inflight session logs (`*_INFLIGHT.md`), or machine-specific absolute paths to git tracking.
