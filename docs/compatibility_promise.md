# KruschNexus Semantic Versioning & Compatibility Promise

> **Policy Version**: 1.0  
> **Effective Release Window**: v0.2.3 through v0.3.x  
> **Status**: Frozen Contract  

---

## 1. Scope & Guarantee

KruschNexus guarantees backward compatibility for all core public contracts from version `0.2.3` up to but not including `0.4.0`. Downstream applications (including `krusch-law`, `krusch-biz`, and external agent harnesses) can rely on these schemas without breaking changes.

A breaking change is defined as:
- Removing or renaming any existing field on frozen models (`SearchHit`, `IngestReport`, `Citation`, `StructuredLocator`).
- Changing the type or nullability of an existing frozen field.
- Removing or altering existing REST endpoints (`/v1/ingest`, `/v1/search`, `/v1/workspaces`, `/v1/documents`, `/health`).
- Renaming public MCP tool names or required input arguments.

Any breaking change will trigger a minor version bump (in pre-1.0 SemVer) or major bump, never a patch commit.

---

## 2. Frozen Data Models

### 2.1 `SearchHit` (v1.0 Frozen Schema)
The following fields are strictly frozen through `0.3.x`:

| Field Name | Type | Description |
| :--- | :--- | :--- |
| `schema_version` | `str` | Must equal `"1.0"` |
| `citation` | `str` | Canonical citation string (`doc.pdf p.N § Header`) |
| `page_number` | `Optional[int]` | 1-based page number for paged docs; `None` for unpaged |
| `header` | `Optional[str]` | Heading text or structural locator |
| `locator` | `Optional[str]` | Breadcrumb path or row range |
| `structured_locator` | `Optional[StructuredLocator]` | Typed locator object |
| `score` | `float` | Final blended rank score |
| `text` | `str` | Unmodified chunk text body |
| `document_id` | `int` | Primary relational document identifier |
| `chunk_id` | `int` | Relational chunk identifier |
| `filename` | `Optional[str]` | Sanitized filename |
| `workspace` | `Optional[str]` | Origin workspace |
| `chunk_index` | `Optional[int]` | 0-based sequence within document |
| `phrase_boost` | `bool` | True if boosted by quoted phrase match |
| `lexical_boost` | `bool` | Alias of `phrase_boost` |
| `section_boost` | `bool` | True if boosted by statutory section match |
| `heading_path` | `List[str]` | Hierarchical breadcrumbs |
| `match_reasons` | `List[str]` | Deterministic explainability tags |
| `char_start` | `Optional[int]` | Character start offset within page/section |
| `char_end` | `Optional[int]` | Character end offset within page/section |
| `bbox` | `Optional[List[float]]` | Bounding box `[left, top, width, height]` in PDF points |
| `confidence` | `Optional[float]` | Mean OCR confidence (0.0 to 1.0) |
| `used_ocr` | `bool` | True if text was derived from OCR fallback |
| `doc_type` | `Optional[str]` | Classification enum value |
| `score_vector` | `Dict[str, Any]` | Component rank breakdown |

### 2.2 `IngestReport` (v1.0 Frozen Schema)
The following fields are strictly frozen through `0.3.x`:

| Field Name | Type | Description |
| :--- | :--- | :--- |
| `schema_version` | `str` | Must equal `"1.0"` |
| `status` | `str` | `"completed"`, `"skipped_duplicate"`, or `"failed"` |
| `document_id` | `Optional[int]` | Created or existing document record ID |
| `filename` | `str` | Sanitized original filename |
| `workspace` | `str` | Target workspace name |
| `file_hash` | `str` | SHA-256 content digest of source file |
| `doc_type` | `str` | Classification category |
| `parser_name` | `str` | Identifier of dispatch parser |
| `parser_version` | `str` | Version string of dispatch parser |
| `detected_mime` | `str` | True MIME type detected from magic bytes |
| `total_pages` | `int` | Page count of document |
| `total_chunks` | `int` | Number of chunks generated and committed |
| `pages` | `int` | Alias of `total_pages` |
| `chunks` | `int` | Alias of `total_chunks` |
| `ocr_pages` | `List[int]` | Page numbers where OCR was applied |
| `ocr_confidence` | `Dict[int, float]` | Per-page OCR confidence scores |
| `duration_ms` | `float` | Total elapsed pipeline duration |
| `warnings` | `List[str]` | Typed warning codes emitted |
| `error` | `Optional[str]` | Redacted error message on failure |

---

## 3. Storage & Relational Invariants

1. **Workspace Isolation**: Database records in `documents`, `document_chunks`, and `ingest_runs` are permanently scoped by `workspace_id`.
2. **Idempotency**: Ingesting an identical `(workspace, file_hash, parser_version, embed_model, embed_dim)` tuple is an idempotent no-op returning `status: "skipped_duplicate"`.
3. **Format-Honest Locators**: Paged formats (PDF) populate `page_number`; non-paged formats (DOCX, HTML, EML, CSV) set `page_number = None` and address via heading/row paths. No format ever emits `"p. None"`.

---

## 4. Contract Snapshot Enforcement in CI

Automated tests in `tests/unit/test_api_contract.py` assert `SearchHit.model_json_schema()` and `IngestReport.model_json_schema()` against committed golden JSON snapshots in `tests/unit/contracts/`. Any modification to frozen properties without a corresponding version increment will immediately fail CI.
