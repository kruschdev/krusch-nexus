# KruschNexus Local Document Ingestion & Search FastMCP Server

The **KruschNexus MCP Server** serves as the Model Context Protocol (MCP) gateway for closed-loop document ingestion, structural chunking, and hybrid vector/full-text retrieval. It enables local AI agents (Claude Desktop, Cursor, Antigravity, OpenClaw) to ingest unstructured files and perform citation-grounded searches directly against PostgreSQL/pgvector or SQLite.

---

## 🏛️ Architectural Invariants

- **100% Air-Gapped & Local**: Uses Poppler `pdftotext`, local Tesseract OCR fallback, and local Ollama `bge-large` embeddings. No external cloud API egress.
- **Strict Provenance**: Every chunk preserves exact 1-based page numbers (`p. N`), section heading breadcrumbs, and SHA-256 content hashes.
- **Structured Citations**: Searches return structured dictionaries (`filename`, `page_number`, `header`, `locator`) alongside formatted strings to avoid hallucinated citations.
- **Decoupled Spine**: Serves downstream applications (KruschLaw for legal workflows, KruschBiz for corporate ops) without polluting the core ingestion engine.

---

## 🔌 Canonical MCP Tool Catalog

### 1. User Tools (Safe Standard Operations)

- **`nexus_ingest_file(file_path, workspace_name, doc_type, archive)`**
  - **Description**: Ingest a local document (PDF with automated local OCR fallback, DOCX, EML, CSV, HTML, TXT/MD). Preserves 1-based page numbers, computes SHA-256 hashes, generates 1024d local embeddings, and stores structural chunks with HNSW & tsvector indexes.
  - **Parameters**:
    - `file_path` (str, required): Absolute or relative path to the local document. Must reside within `allowed_ingest_roots`.
    - `workspace_name` (str, required): Target matter or workspace.
    - `doc_type` (str, optional, default: `"general"`): Category (`"authority"`, `"work_product"`, `"fact_narrative"`, `"general"`).
    - `archive` (bool, optional, default: `False`): If `True`, moves the file safely into `.ingested/<workspace>/` upon commit.
  - **Returns**: JSON Ingest Report with page count, chunk count, OCR status, and processing duration.

- **`nexus_get_ingest_report(doc_id_or_hash)`**
  - **Description**: Retrieve the stored `IngestReport` for any document by database ID or SHA-256 hash.

- **`nexus_search_corpus(query, workspace_name, doc_type, limit)`**
  - **Description**: Execute hybrid vector (HNSW cosine) + full-text Reciprocal Rank Fusion (RRF) search with statutory section boosting and exact quote phrase boosting.
  - **Parameters**:
    - `query` (str, required): Natural language or keyword query (supports exact quotes e.g. `"liquidated damages"` and legal citations e.g. `§ 1950.5`).
    - `workspace_name` (str, required): Mandatory target workspace. Global multi-workspace search is disallowed.
    - `doc_type` (str, optional): Filter by document category.
    - `limit` (int, optional, default: 5): Maximum number of top chunks to return.
  - **Returns**: Structured results with `score`, `text`, `citation` (object with `filename`, `page_number`, `header`, `locator`), and `explainability`.

- **`nexus_list_workspaces()`**
  - **Description**: List all active workspaces and their document counts in the database.

- **`nexus_list_documents(workspace, limit)`**
  - **Description**: List ingested documents and metadata (page counts, chunk counts, hashes) in a workspace.

- **`nexus_doctor()`**
  - **Description**: Run a diagnostic environment audit inspecting Poppler binaries, Tesseract OCR, database vector extensions, and Ollama embedding status.

---

### 2. Operator Tools (Destructive Guarded Actions)

- **`nexus_reparse(document_id, operator_confirmed, operator_token)`**
  - **Description**: Re-parse and re-index an existing document from archive.
  - **Guards**: Requires explicit `operator_confirmed=True`.

- **`nexus_delete_document(document_id, operator_confirmed, operator_token)`**
  - **Description**: Delete a document and its chunks from the database.
  - **Guards**: Requires explicit `operator_confirmed=True`.

---

## 🛠️ Client Configuration

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "krusch-nexus": {
      "command": "python3",
      "args": ["-m", "krusch_nexus.mcp"],
      "cwd": "/path/to/krusch-nexus",
      "env": {
        "DATABASE_URL": "postgresql://krusch:<your_secure_password>@127.0.0.1:5432/krusch_nexus_db",
        "OLLAMA_BASE_URL": "http://127.0.0.1:11434"
      }
    }
  }
}
```

### Cursor / Antigravity IDE (`.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "krusch-nexus": {
      "command": "python3",
      "args": ["-m", "krusch_nexus.mcp"],
      "cwd": "/path/to/krusch-nexus",
      "env": {
        "DATABASE_URL": "postgresql://krusch:<your_secure_password>@127.0.0.1:5432/krusch_nexus_db",
        "OLLAMA_BASE_URL": "http://127.0.0.1:11434"
      }
    }
  }
}
```

### Remote Network (SSE Mode)

Run the server with SSE transport:
```bash
MCP_TRANSPORT=sse MCP_PORT=8002 python3 -m krusch_nexus.mcp
```

Connect remote clients via URL:
```json
{
  "mcpServers": {
    "krusch-nexus": {
      "url": "http://127.0.0.1:8002/sse",
      "transport": "sse"
    }
  }
}
```
