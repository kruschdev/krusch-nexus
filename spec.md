# KruschNexus — System Specification

> **Version**: 1.0.0  
> **Status**: Production Core  
> **Architecture**: Local-First / Air-Gapped Document Ingestion & Page-True Citation Spine  
> **Author**: Kevin Ruschman (`ksruschman@gmail.com`)  

---

## 1. Executive Summary

**KruschNexus** is an open-source, local-first document ingestion and corpus indexing engine. It solves the foundational failure point of local RAG: transforming messy, heterogeneous files (scanned PDFs, Word documents, email archives, spreadsheets, HTML, markdown) into structure-aware, citation-preserving vector embeddings with exact 1-based page numbers and section headers.

It operates primarily as an air-gapped **corpus factory** serving downstream domain applications (such as KruschLaw, KruschBiz, and local AI agents) via a FastMCP server, typed Python SDK, or REST API.

---

## 2. Core Invariants

1. **Air-Gapped & Local-First by Default**:
   All core parsing, OCR fallback, structural chunking, embedding generation, and retrieval operate 100% locally without external cloud dependencies.
   - Parsing: Native XML for DOCX, native RFC822 for EML, Poppler `pdftotext -layout` for PDF.
   - OCR Fallback: Local `tesseract-ocr` via Poppler `pdftoppm` for scanned pages (< 30 characters by default).
   - Embeddings: Local Ollama (`bge-large`, 1024-dim) with text hash caching.
   - Database: PostgreSQL with `pgvector` (HNSW cosine index) and full-text search (`tsvector` GIN index).

2. **Citation & Structure Preservation**:
   Every chunk retains:
   - 1-based page number (`page_number`)
   - Document section header breadcrumb (`header`, e.g., `§ 1950.5` or `Section 8.22`)
   - Canonical citation string (e.g., `[contract.pdf, p. 3, § 4.2]`)
   - Source SHA-256 hash for deduplication.

3. **Hardened Watchdog State Machine**:
   - Inotify detection with `.part` lock rename in `staging/`.
   - Explicit failure isolation: Corrupted or unparseable files are moved to `.failed/<workspace>/<name>` with a `<name>.error.json` sidecar. Poison files are never left in watch directories.
   - Non-destructive archival: Processed files are preserved in `.ingested/<workspace>/<hash>-<name>`.

4. **Hybrid Retrieval (Dense + FTS + RRF + Section Boost)**:
   Search combines dense vector similarity (cosine) with PostgreSQL full-text search (`tsvector`), merged using standard Reciprocal Rank Fusion ($k=60$) and statutory section heading boosts.

5. **Client Abstraction & Public API Contracts**:
   External applications do not own document parsers. They consume the corpus through `NexusIngestClient` or the `KruschNexusMCP` server with typed Pydantic models.

---

## 3. Architecture & Data Flow

```
   ┌──────────────────────────────────────────────────────────┐
   │                       Input Sources                      │
   │  - PDF (Vector & Scanned)   - DOCX (Office Open XML)     │
   │  - EML (RFC822 MIME)        - TXT / MD / CSV / JSON      │
   └─────────────────────────────┬────────────────────────────┘
                                 │
                                 ▼
   ┌──────────────────────────────────────────────────────────┐
   │              Ingest State Machine Pipeline               │
   │  1. Inotify Detect -> Move to staging/<workspace>/.part  │
   │  2. Content SHA-256 Check & Deduplication / Alias Check  │
   │  3. Multi-Format Parsers (with local Tesseract OCR)      │
   │  4. Structural Chunking (§, headings, sliding overlap)   │
   │  5. Ollama Batch Embedding (cached by text hash)         │
   │  6. DB Write (atomic Document + DocumentChunk record)    │
   │  7. Success -> .ingested/ | Failure -> .failed/ + sidecar│
   └─────────────────────────────┬────────────────────────────┘
                                 │
                                 ▼
   ┌──────────────────────────────────────────────────────────┐
   │                   PostgreSQL Substrate                   │
   │  - workspaces        (id, name UNIQUE, description)      │
   │  - documents         (workspace_id, filename, file_hash)│
   │  - document_chunks   (embedding vector(1024), tsv GIN)   │
   └─────────────────────────────┬────────────────────────────┘
                                 │
                                 ▼
   ┌──────────────────────────────────────────────────────────┐
   │                    Serving Interfaces                    │
   │  - FastMCP Server    (nexus_* tools via Stdio & SSE)     │
   │  - NexusIngestClient (Typed Python SDK returning models) │
   │  - REST API          (FastAPI /v1/ingest, /v1/search)    │
   └──────────────────────────────────────────────────────────┘
```

---

## 4. Serving Interfaces & Tool Catalog

- **FastMCP Tools**:
  1. `nexus_list_workspaces`
  2. `nexus_list_documents`
  3. `nexus_ingest_file`
  4. `nexus_ingest_directory`
  5. `nexus_get_ingest_report`
  6. `nexus_search_corpus`
- **NexusIngestClient**:
  - `ingest_file(filepath, workspace, doc_type) -> IngestReport`
  - `search(query, workspace, limit) -> List[SearchHit]`
  - `list_workspaces() -> List[WorkspaceInfo]`
  - `list_documents(workspace) -> List[DocumentInfo]`
  - `get_ingest_report(doc_id_or_hash) -> Optional[IngestReport]`
- **REST API (`/v1`)**:
  - `POST /v1/ingest`, `POST /v1/search`, `GET /v1/documents`, `GET /health`

---

## 5. Deployment Model

```bash
docker compose up -d
```
Spins up:
1. `db`: PostgreSQL 16 with `pgvector` pre-installed.
2. `backend`: FastAPI server bound to `127.0.0.1:8000`.
3. `ingest-worker`: Background watch-folder daemon with 4GB memory cap.
4. `mcp-server`: FastMCP SSE server bound to `127.0.0.1:8002`.
