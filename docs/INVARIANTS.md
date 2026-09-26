# 🛡️ KruschNexus Core Invariants & Pass/Fail Test Matrix

> **Authoritative Specification**: This document establishes the 10 non-negotiable architectural invariants of KruschNexus (`projects/krusch-nexus`). Every invariant is paired with deterministic, pass/fail automated regression tests.

---

## 📋 The Invariants Matrix

| # | Invariant Name | Failure Mode if Violated | Enforcing Test(s) | Status |
|---|---|---|---|---|
| **INV-1** | **Air-Gap & Zero Cloud Egress** | Sensitive documents or queries leak to third-party cloud APIs (OpenAI, Anthropic, cloud embeddings) | `tests/test_invariants.py::TestKruschNexusInvariants::test_inv_01_air_gap_zero_cloud_egress` | ✅ PASS |
| **INV-2** | **Page-Faithful Provenance** | Citations hallucinate page numbers or lack verifiable spatial coordinates/locators | `tests/test_invariants.py::TestKruschNexusInvariants::test_inv_02_page_faithful_citation` | ✅ PASS |
| **INV-3** | **Structure-First Chunking** | Arbitrary token splitting cuts through sentences, tables, or destroys heading hierarchy | `tests/test_invariants.py::TestKruschNexusInvariants::test_inv_03_structure_first_chunking` | ✅ PASS |
| **INV-4** | **Strict Workspace Isolation** | Multi-tenant or cross-domain corpus leakage between isolated workspaces | `tests/test_invariants.py::TestKruschNexusInvariants::test_inv_04_strict_workspace_isolation` | ✅ PASS |
| **INV-5** | **Strict Loopback Residency** | Ingestion server or daemon binds to public network interfaces without perimeter auth | `tests/test_invariants.py::TestKruschNexusInvariants::test_inv_05_strict_loopback_residency` | ✅ PASS |
| **INV-6** | **Pre-Spool Magic-Byte Gate** | Malicious binary executables (PE, ELF, Mach-O) or HTML disguised as PDFs exploit parsers | `tests/test_invariants.py::TestKruschNexusInvariants::test_inv_06_pre_spool_magic_bytes_verification` | ✅ PASS |
| **INV-7** | **Idempotent Content Hashing** | Redundant ingestion inflates vector database, corrupts statistics, or duplicates chunks | `tests/test_invariants.py::TestKruschNexusInvariants::test_inv_07_idempotent_hashing_deduplication` | ✅ PASS |
| **INV-8** | **Dual-Engine Vector Interop** | Code crashes on SQLite due to pgvector type mismatches or lacks offline portability | `tests/test_invariants.py::TestKruschNexusInvariants::test_inv_08_universal_vector_dual_engine` | ✅ PASS |
| **INV-9** | **Resilient 8-State Ledger** | Process crash during ingestion leaves orphaned rows, unindexed chunks, or untracked state | `tests/test_invariants.py::TestKruschNexusInvariants::test_inv_09_resilient_state_machine_ledger` | ✅ PASS |
| **INV-10** | **Operator-Token-Gated Purge** | Unauthenticated callers delete documents or reparse without cryptographically verified audit | `tests/test_invariants.py::TestKruschNexusInvariants::test_inv_10_operator_token_gated_destruction`<br>`tests/unit/test_nexus_properties.py::TestNexusProperties::test_operator_audit_append_only_immutability` | ✅ PASS |
| **INV-11** | **Legal Hold Preservation Gating** | Spoliation of evidence, unauthorized document mutation, or deletion during active legal hold | `tests/unit/test_nexus_properties.py::TestNexusProperties::test_legal_hold_blocks_client_mutations`<br>`tests/unit/test_nexus_properties.py::TestNexusProperties::test_legal_hold_http_423_locked_gating`<br>`tests/unit/test_nexus_properties.py::TestNexusProperties::test_export_legal_hold_bundle_integrity` | ✅ PASS |

---

## 🔍 Detailed Invariant Specifications

### INV-1: Air-Gap & Zero Cloud Egress
* **Requirement**: KruschNexus must operate in complete sovereign isolation. Ingestion, OCR, parsing, chunking, and embedding generation must never make unauthenticated egress calls to public third-party LLM providers.
* **Behavior**: Default embedding generation routes to local Ollama (`nomic-embed-text`), local fastembed, or deterministic dummy embeddings (`embed_backend="dummy"`). Network attempts to external cloud APIs without explicit proxy configuration are strictly blocked.
* **Verification Command**:
  ```bash
  pytest tests/test_invariants.py -k "test_inv_01_air_gap_zero_cloud_egress"
  ```

### INV-2: Page-Faithful Provenance & Bounding Box Preservation
* **Requirement**: Every ingested chunk and returned search hit must retain deterministic provenance back to the source physical document.
* **Behavior**: Search hits include 1-indexed human-readable `page_number`, canonical `citation` string (e.g. `contract.pdf, p.1`), character offsets (`char_start`, `char_end`), and bounding box coordinates (`bbox: [left, top, width, height]`) when extracted from structured PDF blocks.
* **Verification Command**:
  ```bash
  pytest tests/test_invariants.py -k "test_inv_02_page_faithful_citation"
  ```

### INV-3: Structure-First Chunking & Hierarchy Preservation
* **Requirement**: Documents must be partitioned along structural and semantic boundaries (headings, articles, sections, tables, paragraphs) rather than arbitrary byte or token slicing.
* **Behavior**: Chunker detects subsection boundaries, tracks the breadcrumb heading stack (`heading_path`), and associates each chunk with its governing `header`. Running headers, footers, and page numbers are recognized and suppressed from contaminating chunk text.
* **Verification Command**:
  ```bash
  pytest tests/test_invariants.py -k "test_inv_03_structure_first_chunking"
  ```

### INV-4: Strict Multi-Tenant / Workspace Isolation
* **Requirement**: Cross-workspace data leakage is strictly prohibited. Workspaces partition all documents, chunks, vectors, full-text indices, and queries.
* **Behavior**: Ingestion and search APIs enforce mandatory `workspace` parameter (`WorkspaceRequiredError`). Queries executed in Workspace A return strictly zero results from Workspace B, even when documents share identical content or filenames.
* **Verification Command**:
  ```bash
  pytest tests/test_invariants.py -k "test_inv_04_strict_workspace_isolation"
  ```

### INV-5: Strict Loopback Data Residency
* **Requirement**: Default server listeners must bind strictly to local loopback interfaces (`127.0.0.1` or `::1`).
* **Behavior**: `NexusConfig.api_host` defaults to `"127.0.0.1"`. Public host binding (`0.0.0.0`) is prohibited unless explicitly configured by the fleet operator behind perimeter firewall rules.
* **Verification Command**:
  ```bash
  pytest tests/test_invariants.py -k "test_inv_05_strict_loopback_residency"
  ```

### INV-6: Pre-Spool MIME Magic-Byte Verification
* **Requirement**: Ingestion must inspect the raw magic bytes of uploaded files before spooling payloads to disk, allocating staging memory, or invoking file parsers.
* **Behavior**: Reads the first 512 bytes:
  - Rejects Windows PE executables (`MZ` / `0x4D5A`).
  - Rejects Linux ELF executables (`\x7fELF`).
  - Rejects macOS Mach-O binaries (`\xfe\xed\xfa\xce`, `\xcf\xfa\xed\xfe`).
  - Rejects HTML or JavaScript masquerading as PDF (`<html>`, `<!doctype`, `<script`).
  - Raises `UnsupportedMimeError` prior to file persistence.
* **Verification Command**:
  ```bash
  pytest tests/test_invariants.py -k "test_inv_06_pre_spool_magic_bytes_verification"
  ```

### INV-7: Idempotent Hashing & Content Deduplication
* **Requirement**: Documents are uniquely identified by their SHA-256 content checksum. Redundant ingestion must not duplicate chunk vectors or create stale duplicates.
* **Behavior**: If a document with an identical SHA-256 hash has already been successfully ingested into the target workspace, the pipeline returns `status="skipped_duplicate"`, references the existing `document_id`, and bypasses redundant chunking and embedding.
* **Verification Command**:
  ```bash
  pytest tests/test_invariants.py -k "test_inv_07_idempotent_hashing_deduplication"
  ```

### INV-8: Deterministic Dual-Engine Vector Support
* **Requirement**: The system must run interchangeably on SQLite (for zero-dependency CLI, library mode, unit tests, and air-gapped workstations) and PostgreSQL with pgvector (for high-throughput multi-tenant fleet servers) without code modification.
* **Behavior**: Uses `UniversalVector(TypeDecorator)`: on PostgreSQL dialects, delegates natively to `pgvector.sqlalchemy.Vector(dim)`; on SQLite dialects, serializes float arrays to JSON strings during parameter binding and deserializes them back to Python lists upon retrieval.
* **Verification Command**:
  ```bash
  pytest tests/test_invariants.py -k "test_inv_08_universal_vector_dual_engine"
  ```

### INV-9: Resilient 8-State Ledger & Crash Recovery
* **Requirement**: Document lifecycle transitions through a discrete, observable finite-state machine.
* **Behavior**: Tracks document progression through states:
  `REGISTERED` → `VALIDATED` → `PARSED` → `CHUNKED` → `EMBEDDED` → `INDEXED` → `COMPLETED` (or `FAILED`).
  Any unexpected crash, out-of-memory kill, or exception records an explicit state transition in `DocumentStateEvent`, allowing operators to audit or recover stalled jobs without corpus corruption.
* **Verification Command**:
  ```bash
  pytest tests/test_invariants.py -k "test_inv_09_resilient_state_machine_ledger"
  ```

### INV-10: Operator-Token-Gated Document Destruction & Auditing
* **Requirement**: Destructive actions (document deletion, reindexing/reparsing, workspace purging) must be authenticated and cryptographically logged.
* **Behavior**: If `NexusConfig.operator_token` is set, calls to `delete_document` or `reparse` without matching tokens raise `AuthenticationError` (HTTP 401/403). Every authorized destructive mutation records an immutable entry in the `OperatorAudit` table enforced by SQLAlchemy event listeners that strictly block `UPDATE` and `DELETE`.
* **Verification Command**:
  ```bash
  pytest tests/unit/test_nexus_properties.py -k "test_operator_audit_append_only_immutability"
  ```

### INV-11: Legal Hold Preservation Gating & Export Bundles
* **Requirement**: Entities under active legal hold (`Workspace.is_legal_hold == True`) are strictly non-deletable and immutable to prevent evidentiary spoliation.
* **Behavior**: Any attempt to delete documents, reparse documents, or purge workspaces under active legal hold raises `LegalHoldActiveError` and returns `HTTP 423 Locked`. Workspaces can export a tamper-evident, signed JSON audit bundle (`export_legal_hold_bundle()`) containing full document manifests, locators, chunk hashes, and operator audit records verified with a root SHA-256 checksum.
* **Verification Command**:
  ```bash
  pytest tests/unit/test_nexus_properties.py -k "test_legal_hold"
  ```

