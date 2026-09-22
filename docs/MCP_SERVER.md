# KruschNexus Local Document Ingestion & Search FastMCP Server

The **KruschNexus MCP Server** serves as the Model Context Protocol (MCP) gateway for closed-loop document ingestion, structural chunking, and hybrid vector/full-text retrieval. It enables local AI agents (Claude Desktop, Cursor, Antigravity, OpenClaw) to ingest messy files and perform citation-grounded searches directly against PostgreSQL/pgvector.

---

## 🏛️ Architectural Invariants

- **100% Air-Gapped & Local**: Uses Poppler `pdftotext`, local Tesseract OCR fallback, and local Ollama `bge-large` embeddings. No external cloud API egress.
- **Strict Provenance**: Every chunk preserves exact 1-based page numbers (`p. N`), section heading breadcrumbs, and SHA-256 content hashes.
- **Decoupled Spine**: Serves downstream applications (KruschLaw for legal workflows, KruschBiz for corporate ops) without polluting the core parser engine.

---

## 🔌 Canonical MCP Tool Catalog

### 1. Ingestion & Archival Tools

- **`nexus_ingest_file(file_path, workspace_name, doc_type, archive)`**
  - **Description**: Ingest a local document (PDF with automated local OCR fallback, DOCX, EML, CSV, HTML, TXT/MD). Preserves 1-based page numbers, computes SHA-256 hashes, generates 1024d local embeddings, and stores structural chunks with HNSW & tsvector indexes.
  - **Parameters**:
    - `file_path` (str, required): Absolute or relative path to the local document.
    - `workspace_name` (str, optional, default: `"General"`): Target matter or workspace.
    - `doc_type` (str, optional, default: `"general"`): Category (`"authority"`, `"work_product"`, `"fact_narrative"`, `"general"`).
    - `archive` (bool, optional, default: `False`): If `True`, moves the file safely into `.ingested/` upon completion.
  - **Returns**: JSON Ingest Report with page count, chunk count, OCR status, and processing duration.

- **`nexus_ingest_directory(directory_path, workspace_name, archive, recursive)`**
  - **Description**: Batch-ingest all supported documents from a directory into the corpus.
  - **Parameters**: `directory_path` (str), `workspace_name` (str), `archive` (bool), `recursive` (bool).
  - **Returns**: Batch summary report with per-document ingestion statistics.

- **`nexus_get_ingest_report(doc_id_or_hash)`**
  - **Description**: Retrieve the detailed processing report for any document by database ID or SHA-256 hash.

---

### 2. Search & Retrieval Tools

- **`nexus_search_corpus(query, workspace_name, doc_type, limit)`**
  - **Description**: Execute hybrid vector (HNSW cosine) + full-text (tsvector) Reciprocal Rank Fusion (RRF) search across all ingested document chunks. Returns exact page/section citations `[filename, p. X, § Section]` and grounded text snippets.
  - **Parameters**:
    - `query` (str, required): Natural language or keyword query.
    - `workspace_name` (str, optional): Restrict search to a specific workspace.
    - `doc_type` (str, optional): Filter by document category.
    - `limit` (int, optional, default: 5): Maximum number of top chunks to return.

- **`nexus_list_workspaces()`**
  - **Description**: List all available workspaces/matters in the database.

- **`nexus_list_documents(workspace_name_or_id, limit)`**
  - **Description**: List ingested documents and metadata (page counts, chunk counts, hashes) in a workspace.

---

### 3. Governance & Review Tools

- **`nexus_classify_document(doc_id, classification_level, allowed_roles)`**
  - **Description**: Set security classification level (`"public"`, `"internal"`, `"confidential"`, `"management_only"`) and role-based access for a document.

- **`nexus_flag_document_for_review(doc_id, reason)`**
  - **Description**: Flag a document as sensitive or suspicious for human review and audit.

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
        "DATABASE_URL": "postgresql://krusch:kruschpassword@localhost:5432/krusch_nexus_db",
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
        "DATABASE_URL": "postgresql://krusch:kruschpassword@localhost:5432/krusch_nexus_db",
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
      "url": "http://10.0.0.85:8002/sse",
      "transport": "sse"
    }
  }
}
```
