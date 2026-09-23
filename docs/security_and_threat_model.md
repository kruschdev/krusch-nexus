# KruschNexus Security & Threat Model

This document outlines the security architecture, trust boundaries, and threat model of **KruschNexus**.

---

## 1. Core Security Thesis

KruschNexus is engineered for **strict air-gapped homelab and enterprise private-cloud deployments**. It assumes that:
- Host networks may transition from isolated subnets to shared LAN environments.
- Ingested files may originate from untrusted external sources (e.g., opposing counsel discovery, vendor submissions).
- AI agent harnesses may be autonomous and must be bounded by strict tenant access controls, path sandboxes, and operator confirmation guards.

**Air-gapped is a deployment mode, not a security proof.** All internal components enforce defense-in-depth regardless of network isolation.

---

## 2. Threat Vectors & Mitigations

### 2.1 Arbitrary File Ingestion & Path Traversal
* **Risk**: An attacker or rogue agent sends a file path such as `/etc/shadow`, `~/.ssh/id_rsa`, or `../../sensitive.key` to an MCP tool or REST API.
* **Mitigation**:
  - `src/krusch_nexus/ingest.py` resolves canonical realpaths and validates against strictly denied system roots (`/etc`, `/proc`, `/sys`, `/dev`, `~/.ssh`, `~/.gnupg`).
  - File paths must reside within explicitly configured `allowed_ingest_roots` (e.g., `ingest_watch/`, fixture directories).
  - Relative traversal sequences (`..`) and symlink escapes are resolved and rejected with `PathSandboxError`.

### 2.2 Multi-Tenant Matter Isolation (Cross-Workspace Leakage)
* **Risk**: A query intended for `General` or `Matter_Jones` retrieves privileged attorney-client work product or confidential financial data from `Matter_Smith`.
* **Mitigation**:
  - `workspace_id` is a **mandatory hard predicate** across all retrieval paths (dense vector ANN, sparse PostgreSQL FTS, SQLite fallback, and document listing).
  - Global cross-workspace queries without an explicit tenant key are rejected with `WorkspaceRequiredError`.
  - Zero-leakage invariant is continuously enforced by CI evaluation tests (`test_workspace_isolation_zero_leakage`).

### 2.3 Malicious & Malformed Document Streams
* **Risk**: Exploits targeting parser buffer overflows or infinite recursion in PDF/DOCX engines.
* **Mitigation**:
  - **Encrypted PDFs**: Inspected via `pdfinfo` and trailer markers before processing; encrypted files fail-closed immediately with `EncryptedPdfError`.
  - **Memory & Resource Quotas**: Configurable limits on maximum page count (`max_page_count=500`), file size (`max_file_size_bytes=50MB`), and subprocess timeouts (30s).
  - **Poison File Quarantine**: Unparseable or malicious files are quarantined to `.failed/<workspace>/` with redacted `.error.json` sidecars containing error class details without leaking document text or credentials.
  - **Safe Execution**: All external commands (`pdftotext`, `pdftoppm`, `tesseract`) execute via list-based `subprocess.run(..., check=True, timeout=...)` with zero shell interpolation.

### 2.4 Credential & Socket Exposure
* **Risk**: Default credentials or open socket bindings expose database or APIs to the local area network.
* **Mitigation**:
  - **Localhost Default**: All services (FastAPI on `:8000`, FastMCP on `:8002`, PostgreSQL on `:5432`) bind strictly to `127.0.0.1` unless explicitly overridden.
  - **Insecure Password Rejection**: Server startup halts if `POSTGRES_PASSWORD` or database connection strings contain insecure defaults (`password`, `kruschpassword`, `admin`, `postgres`, `root`).
  - **Constant-Time Token Validation**: API and MCP endpoints enforce constant-time bearer token validation (`secrets.compare_digest`) when `NEXUS_API_TOKEN` is configured.

### 2.5 Outbound Egress Prevention
* **Risk**: Accidental telemetry or cloud API keys leaking confidential document snippets.
* **Mitigation**:
  - Zero cloud API requirements in the core pipeline; embedding is served exclusively via local Ollama instances (`bge-large`).
  - CI includes a strict grep gate failing on any cloud vendor tokens (`google`, `gemini`, `openai`, `anthropic`).
  - `nexus doctor` checks outbound internet routing and raises a warning/error if external connectivity is detected in air-gapped mode.

---

## 3. Compromised MCP Client Boundaries: What an AI Agent Can & Cannot Do

When an LLM agent (e.g., Claude Desktop, Cursor, Antigravity) connects to KruschNexus via MCP, it operates within strict security boundaries:

| Action Category | Permitted to AI Agent | Prohibited / Operator Guarded |
| :--- | :--- | :--- |
| **Ingestion** | Ingest supported documents inside `allowed_ingest_roots` into a specified workspace. | Ingest files from arbitrary system paths (`/etc`, `~/.ssh`), traversals (`../`), or unsupported extensions (`.exe`). |
| **Search & Retrieval** | Execute hybrid queries against a single, explicitly designated workspace. | Query across multiple workspaces or search globally without specifying `workspace_name`. |
| **Corpus Inspection** | List workspaces, list documents in a workspace, fetch ingest reports by document ID or hash. | Access raw database connection strings, credentials, or internal configuration dumps. |
| **System Diagnostics** | Run `nexus_doctor` to check local binary health and vector extension availability. | Modify server-level configuration or alter allowed roots. |
| **Reparse Document** | ❌ Blocked by default. | Requires explicit `operator_confirmed=True` flag and optional operator token. |
| **Delete Document** | ❌ Blocked by default. | Requires explicit `operator_confirmed=True` flag and optional operator token. |
| **Sidecars & Logs** | View structured error reports in `.failed/`. | Access raw document contents or credentials through sidecar files. |
