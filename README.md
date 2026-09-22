# ⚡ KruschNexus

> **Air-Gapped Document Ingestion Engine & Page-True Citation Spine**  
> *A dedicated corpus factory: file → parse → chunk → embed → persist → search with citations.*

---

## 1. Prerequisites (Host Binaries)

KruschNexus runs fully offline and requires the following host binaries:

- **Poppler Utilities** (`pdftotext`, `pdftoppm`): For layout-preserving PDF page extraction and rendering.
  ```bash
  sudo apt-get install poppler-utils
  ```
- **Tesseract OCR** (`tesseract` + `tesseract-ocr-eng`): For local OCR fallback on scanned or image-only pages.
  ```bash
  sudo apt-get install tesseract-ocr tesseract-ocr-eng
  ```
- **Ollama**: Local embedding host serving `bge-large` (1024 dimensions).
  ```bash
  ollama pull bge-large
  ```
- **PostgreSQL 16 with pgvector**: Relational storage with HNSW cosine (`vector(1024)`) and GIN `tsvector` indexes.

---

## 2. Quickstart

### Option A: Docker Compose (Recommended)
```bash
# 1. Configure environment passwords
cp .env.example .env
# Edit .env and set a secure POSTGRES_PASSWORD

# 2. Launch PostgreSQL, FastAPI, Ingestion Worker, and FastMCP
docker compose up -d

# 3. Verify air-gap and local node connectivity
docker compose exec backend nexus verify --offline
```

### Option B: Bare-Metal / Local Virtualenv
```bash
# 1. Install package in editable mode
pip install -e .

# 2. Run test and eval suites
make test
make eval

# 3. Start the watch daemon or MCP server
nexus daemon --watch-dir ./ingest_watch
nexus mcp
```

---

## 3. Drop a File to Ingest

Drop any supported file (**PDF, scanned PDF, DOCX, EML, HTML, CSV, TXT, MD**) into a workspace subdirectory inside `ingest_watch/`:

```bash
mkdir -p ingest_watch/Matter_Smith
cp /path/to/lease_agreement.pdf ingest_watch/Matter_Smith/
```

The daemon automatically acquires a `.part` staging lock, computes the SHA-256 hash, parses page boundaries, generates local `bge-large` embeddings, writes relational records, and archives the file to `.ingested/Matter_Smith/`.

### Ingest Report (Sample Output)
```json
{
  "status": "completed",
  "document_id": 42,
  "filename": "lease_agreement.pdf",
  "workspace": "Matter_Smith",
  "file_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "doc_type": "authority",
  "total_pages": 14,
  "total_chunks": 28,
  "ocr_pages": [1],
  "duration_ms": 342.15,
  "citation_preview": "lease_agreement.pdf p.1 § 8.22 Permitted Use of Premises"
}
```

---

## 4. Search with Page-True Citations

### CLI Search
```bash
nexus search "liquidated damages" --workspace Matter_Smith
```

### Sample Hit
```
Found 1 hit(s) in workspace 'Matter_Smith':
============================================================

[1] Citation: lease_agreement.pdf p.14 § 14.1 Liquidated Damages (Score: 0.0658)
    Header:   Section 14.1 Liquidated Damages
    Content:  [lease_agreement.pdf - p.14] Section 14.1 Liquidated Damages

              The parties agree that in the event of default, liquidated damages
              shall be assessed at fifty thousand dollars ($50,000)...
============================================================
```

### Python SDK (`krusch_nexus`)
```python
from krusch_nexus import Nexus

nx = Nexus.from_env()

# Ingest
report = nx.ingest("/data/lease.pdf", workspace="Matter_Smith", doc_type="authority")

# Search
hits = nx.search("liquidated damages", workspace="Matter_Smith", limit=5)
for hit in hits:
    print(f"Citation: {hit.citation} | RRF Score: {hit.rrf_score}")
    print(hit.content)
```

---

## 5. Model Context Protocol (MCP) Configuration

To connect KruschNexus directly to **Claude Desktop**, **Cursor**, or any MCP-compatible agent:

### Claude Desktop (`claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "krusch-nexus": {
      "command": "python",
      "args": [
        "-m",
        "krusch_nexus.mcp"
      ],
      "env": {
        "DATABASE_URL": "postgresql://krusch:your_password@localhost:5432/krusch_nexus_db",
        "OLLAMA_EMBED_HOST": "http://127.0.0.1:11434"
      }
    }
  }
}
```

### Available Tools:
- `nexus_search_corpus(query, workspace_name, limit=5)`: Hybrid search with canonical `{filename} p.{n} § {header}` citations.
- `nexus_ingest_file(file_path, workspace_name, doc_type)`: Ingest a document file into an isolated workspace.
- `nexus_ingest_directory(directory_path, workspace_name)`: Batch-ingest an entire directory.
- `nexus_get_ingest_report(doc_id_or_hash)`: Retrieve detailed ingestion provenance and OCR stats.
- `nexus_list_workspaces()`: List workspaces and indexed document counts.
- `nexus_list_documents(workspace_name)`: List documents within a workspace.
