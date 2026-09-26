# KruschNexus — Stable Contract Specification (`STABLE_CONTRACT.md`)

> **Version**: 0.2.4 (SemVer Guaranteed through 0.3.0)  
> **Status**: Frozen Public Contract  
> **Target Audience**: Downstream Consumers (`krusch-law`, `krusch-biz`, `krusch-bizlaw`, `krusch-gateway-mcp`), Integration Engineers

---

## 1. Frozen Public Data Models

All public data models are strictly versioned with `schema_version = "1.0"`. Fields and properties are validated against golden JSON schema snapshots in `tests/unit/contracts/` on every CI run.

### `SearchHit` (v1.0)
The immutable search result object returned by `NexusClient.search()`:

| Field | Type | Guarantee / Description |
|---|---|---|
| `schema_version` | `str` | Always `"1.0"` |
| `citation` | `str` | Canonical citation string: `{file} p.{N} § {hdr}` (never emits `p. None`) |
| `page_number` | `int \| None` | 1-based page index (None only for unpaged markdown/DOCX/CSV) |
| `header` | `str \| None` | Nearest preceding heading or section title |
| `locator` | `str \| None` | Breadcrumb path (e.g. `"Art. IV > Sec. 8.22"`) |
| `structured_locator` | `StructuredLocator` | Parsed locator object (`kind`, `page`, `path`, `char_span`, `bbox`) |
| `score` | `float` | Final RRF score after capped section/phrase boosts |
| `text` | `str` | Substantive chunk body text (running headers and noise stamps stripped) |
| `document_id` | `int` | Foreign key to `documents.id` |
| `chunk_id` | `int` | Foreign key to `document_chunks.id` |
| `char_start` / `char_end`| `int \| None` | Exact 0-based character offsets within the page |
| `bbox` | `list[float] \| None` | PDF point bounding box `[left, top, width, height]` (or None) |
| `match_reasons` | `list[str]` | Explainability tokens: `["vector_match", "fts_match", "phrase_boost", "section_boost"]` |

### `IngestReport` (v1.0)
The immutable ingestion audit report returned by `NexusClient.ingest()`:

| Field | Type | Guarantee / Description |
|---|---|---|
| `status` | `str` | `"completed"` \| `"skipped_duplicate"` \| `"failed"` |
| `document_id` | `int \| None` | Database document ID if persisted |
| `filename` | `str` | Sanitized original filename |
| `workspace` | `str` | Partitioned multi-tenant workspace |
| `file_hash` | `str` | SHA-256 digest of original source file |
| `pages` / `chunks` | `int` | Total count of indexed pages and content chunks |
| `ocr_pages` | `list[int]` | Exact list of page numbers that triggered OCR fallback |
| `ocr_confidence` | `dict[int, float]` | Word-level mean confidence scores per OCR'd page |
| `ocr_trigger_reasons` | `dict[int, str]` | Reason per page: `"none"`, `"sparse_text"`, `"image_xobject"`, `"forced"` |
| `duration_ms` | `float` | Total end-to-end ingestion latency in milliseconds |
| `duration_breakdown_ms` | `dict[str, float]`| Sub-stage timings: `{"parse": X, "chunk": Y, "embed": Z, "db_commit": W}` |
| `citation_preview` | `str \| None` | First substantive citation generated for the document |

---

## 2. Canonical Citation Format Standard

KruschNexus guarantees format honesty across document types:

- **Paged Instruments (PDF)**:
  `{filename} p.{page} § {header}`  
  *Example*: `Master_Lease_2024.pdf p.14 § Section 8.22`
- **Unpaged Prose (DOCX / HTML / Markdown)**:
  `{filename} § {heading_path}`  
  *Example*: `Employee_Handbook.docx § Article IV > Code of Conduct`
- **Tabular Data (CSV / TSV)**:
  `{filename} Rows {start}-{end}`  
  *Example*: `vendor_matrix.csv Rows 12-24`
- **Invariant**: The engine **never** emits `'p. None'`, `'p. 0'`, or undefined locator fallbacks.

---

## 3. FastMCP Tool Surface (10 Tools)

| Tool Name | Class | Required Auth / Safeguard | Core Responsibility |
|---|---|---|---|
| `nexus_list_workspaces` | User | Token ACL | List available document workspaces |
| `nexus_list_documents` | User | Workspace token | List indexed documents within a workspace |
| `nexus_ingest_file` | User | Workspace token | Ingest single file with SHA-256 verification |
| `nexus_ingest_directory` | User | Workspace token | Recursively ingest supported directory files |
| `nexus_get_ingest_report`| User | None | Retrieve provenance report by hash or ID |
| `nexus_search_corpus` | User | Workspace token | Multi-tenant hybrid search with citation spine |
| `nexus_export_workspace` | User | Workspace token | Export workspace to standalone `.tar.gz` bundle |
| `nexus_import_workspace` | User | Workspace token | Import workspace bundle with Tar Slip protection |
| `nexus_reparse` | Operator | Typed confirmation token (`CONFIRM_REPARSE_<id>`) | Re-run parser/chunker without file transfer |
| `nexus_delete_document`| Operator | Typed confirmation token (`CONFIRM_DELETE_<id>`) | Hard-delete document and cascade chunks |

---

## 4. Security & Safety Invariants

1. **Fail-Closed on Encryption (INV-1)**: Password-protected or encrypted PDFs fail closed immediately with `EncryptedPdfError` (HTTP 422).
2. **Pre-Spool Magic-Byte Gate (INV-7)**: File headers are validated against declared MIME types (e.g. `%PDF-`, `PK\x03\x04`). Shell scripts, executables (`MZ`, `ELF`), and HTML disguised as PDFs are rejected before disk spooling.
3. **Legal Hold Lock (INV-11)**: Workspaces flagged with `is_legal_hold=True` reject deletion, reparse, and purge operations with `HTTP 423 Locked`.
4. **Append-Only Operator Audit**: Database events record every administrative operation; historical audit logs cannot be updated or deleted.
5. **Path Sandbox**: All file accesses are restricted to configured allowed ingest roots. Directory traversals (`../../`) trigger `PathSandboxError`.
