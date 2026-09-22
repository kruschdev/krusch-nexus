# KruschNexus — System Specification

> **Version**: 1.0.0  
> **Status**: Production Core  
> **Architecture**: Local-First / Air-Gapped Document Ingestion & Hybrid RAG Engine  
> **Author**: kruschDev  

---

## 1. Executive Summary

**KruschNexus** is an open-source, local-first document ingestion and corpus indexing engine. It solves the foundational failure point of local RAG: transforming messy, heterogeneous files (scanned PDFs, Word documents, email archives, spreadsheets, HTML, markdown) into structure-aware, citation-preserving vector embeddings with exact 1-based page numbers and section headers.

It operates primarily as a **corpus factory** serving downstream domain applications (such as legal assistants, business intelligence, and local AI agents) via a FastMCP server, Python client library, or REST API.

---

## 2. Core Invariants

1. **Air-Gapped & Local-First by Default**:
   All core parsing, OCR fallback, structural chunking, embedding generation, and retrieval operate 100% locally without external cloud dependencies.
   - Parsing: Native XML for DOCX, native RFC822 for EML, Poppler `pdftotext -layout` for PDF.
   - OCR Fallback: Local `tesseract-ocr` via Poppler `pdftoppm` for scanned pages (< 30 characters).
   - Embeddings: Local Ollama (`bge-large`, 1024-dim) with text hash caching.
   - Database: PostgreSQL with `pgvector` (HNSW cosine index) and full-text search (`tsvector` GIN index).

2. **Citation & Structure Preservation**:
   Every chunk retains:
   - 1-based page number (`page_num`)
   - Document section header breadcrumb (`header`, e.g., `§ 1950.5` or `### 2.1 Protocol`)
   - Canonical citation string (e.g., `[contract.pdf, p. 3, § 4.2]`)
   - Source SHA-256 hash for deduplication.

3. **Safe Watch-Folder Archival**:
   The ingestion daemon monitors the watch directory, processes new files, and moves them safely to `.ingested/` with timestamping and SHA-256 verification. Source files are never deleted destructively.

4. **Hybrid Retrieval (RRF)**:
   Search combines dense vector similarity (cosine) with PostgreSQL full-text search (`tsvector`), merged using standard Reciprocal Rank Fusion ($k=60$).

5. **Client Abstraction**:
   External applications do not own document parsers. They consume the corpus through `NexusIngestClient` or the `KruschNexusMCP` server.

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
   │                      Ingest Core                         │
   │  1. Multi-Format Parsers (with local Tesseract OCR)      │
   │  2. Structural Chunking (§, headings, token budgets)     │
   │  3. SHA-256 Hash Caching & Deduplication                 │
   │  4. Safe Watch-Folder Archival (.ingested/)              │
   └─────────────────────────────┬────────────────────────────┘
                                 │
                                 ▼
   ┌──────────────────────────────────────────────────────────┐
   │                   PostgreSQL Substrate                   │
   │  - workspaces        (isolated tenant/project scopes)    │
   │  - documents         (metadata, page counts, SHA-256)   │
   │  - document_chunks   (pgvector 1024d + tsvector GIN)     │
   │  - ingest_reports    (audit logs, timings, chunk stats)  │
   └─────────────────────────────┬────────────────────────────┘
                                 │
                                 ▼
   ┌──────────────────────────────────────────────────────────┐
   │                    Serving Interfaces                    │
   │  - FastMCP Server    (Standard Stdio & SSE for Agents)   │
   │  - NexusIngestClient (Python library for domain apps)    │
   │  - REST API          (FastAPI endpoints /chat, /search)  │
   └──────────────────────────────────────────────────────────┘
```

---

## 4. Extension Boundaries (Pluggable Interfaces)

To ensure the ingestion core remains robust, auditable, and easily deployable, downstream domain applications and clients consume Nexus via standardized interfaces:

- **Nexus FastMCP Server**: Stdio & SSE tool server enabling agents (Claude Desktop, Cursor, Antigravity) to ingest files, directories, search corpus, and inspect reports.
- **NexusIngestClient**: Direct Python client library for domain apps (`krusch-law`, `krusch-biz`, CLI scripts) without IPC overhead.
- **Downstream Applications**: Legal analysis rules and corporate workflows consume Nexus via MCP tools and client APIs, keeping the ingestion spine clean and decoupled.


---

## 5. Standard Deployment Model

### Single-Command Compose
```bash
docker compose up -d
```
Spins up:
1. `db`: PostgreSQL 16 with `pgvector` pre-installed.
2. `backend`: FastAPI server + FastMCP server.
3. `ingest-worker`: Background watch-folder daemon monitoring `./ingest_watch`.

### Homelab Multi-Node Deployment
Homelab operators orchestrating across cluster nodes (e.g., dual GPUs, DBOS background workers) use:
```bash
docker compose -f docker-compose.yml -f docker-compose.homelab.yml up -d
```
