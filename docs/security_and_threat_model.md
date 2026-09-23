# KruschNexus Security & Threat Model

This document specifies the threat model, attack surface, defensive controls, and operational boundaries of **KruschNexus** (v0.2.3).

---

## 1. Threat Model & Attack/Defense Matrix

The table below catalogs threat vectors, realistic attack scenarios in a networked homelab or private server environment, and KruschNexus's hardened technical defenses.

| Threat Category | Attack Vector / Scenario | Impact | Defensive Controls in KruschNexus | Verification & Invariants |
| :--- | :--- | :--- | :--- | :--- |
| **Path Traversal & Ingestion Escape** | Malicious agent or client passes `../../etc/shadow`, `/root/.ssh/id_rsa`, or absolute system paths to ingest endpoints. | Unauthorized ingestion and indexing of host secrets. | • `validate_safe_path` rejects paths outside `ALLOWED_INGEST_ROOTS`.<br>• Denies forbidden roots (`/etc`, `/proc`, `/sys`, `/dev`, `~/.ssh`, `~/.gnupg`).<br>• Rejects symlinks unconditionally without following (`os.path.islink`). | `test_path_sandbox_traversal_rejection`<br>`test_symlink_rejection` |
| **Archive Tar-Slip** | Attacker crafts a `.tar.gz` workspace backup containing entries like `../../../../usr/bin/backdoor`. | Arbitrary file write / RCE on host during workspace import. | • `import_workspace` verifies every member path with `is_relative_to(target_dir)` before extraction.<br>• Rejects any entry attempting directory traversal with `PathSandboxError`. | `test_tar_slip_rejection_on_import` |
| **Cross-Tenant Corpus Leakage** | Tenant A queries a term that matches confidential documents or attorney-client work product in Tenant B's matter. | Confidentiality breach; privilege waiver. | • `workspace_id` is an enforced SQL predicate in dense vector ANN, PostgreSQL FTS, and SQLite fallback.<br>• PostgreSQL kernel-level Row Level Security (RLS) policies on `documents` and `document_chunks`.<br>• Token-to-workspace mapping (`NEXUS_TOKEN_WORKSPACES`). | `test_workspace_isolation_zero_leakage`<br>`test_rls_sql_session_isolation` |
| **Decompression & Image Bombing** | Ingestion of 100,000x100,000 pixel image or 10,000 page PDF designed to exhaust memory during OCR. | DoS / OOM kill of host services. | • Hard blast radius limits: `max_page_count` (500 pages), `max_pixels_per_page` (25 MP), `max_file_size_bytes` (50 MB).<br>• Subprocess timeouts (30s) on Poppler and Tesseract. | `test_image_bomb_rejection`<br>`test_max_pages_enforcement` |
| **Poison Document Pipeline Jamming** | Corrupted PDF with broken cross-reference tables or syntax errors halts batch processing. | Ingest daemon stalls; unprocessed queue piles up. | • 8-state machine captures failure at exact transition.<br>• Quarantines malformed file to `.failed/<workspace>/`.<br>• Writes redacted `.error.json` sidecar without document text.<br>• Daemon reaps stale locks after timeout. | `test_poison_file_quarantine`<br>`test_stale_lock_reaper` |
| **Privilege Escalation via MCP Tools** | AI Agent attempts to delete or alter documents without human operator sign-off. | Irreversible document destruction or tampering. | • Destructive MCP tools (`nexus_delete_document`, `nexus_reparse`) require typed confirmation tokens (e.g. `CONFIRM_DELETE_<id>`).<br>• Optional operator token authentication (`NEXUS_OPERATOR_TOKEN`). | `test_mcp_operator_confirmation_guard` |
| **Network Sieve & Socket Exposure** | Default daemon binding to `0.0.0.0` exposes search and ingest APIs to LAN or Docker bridge. | Unauthenticated API access from unauthorized devices. | • Default binding strictly to `127.0.0.1`.<br>• Mandatory `NEXUS_API_TOKEN` outside `NEXUS_ENV=dev`.<br>• Insecure password detection for PostgreSQL during `nexus doctor`. | `test_doctor_localhost_binding`<br>`test_default_deny_in_prod` |
| **Cloud Telemetry Leakage** | Embedding client silently calls out to third-party cloud APIs (Google, OpenAI, Anthropic). | Legal corpus data sent to external cloud LLM providers. | • Air-gap enforcement (`AIRGAP=1` by default).<br>• Startup check halts if cloud API keys (`OPENROUTER_API_KEY`) are set unless `ALLOW_CLOUD=1`.<br>• Embeddings restricted to local Ollama (`bge-large`). | `test_airgap_startup_probe`<br>`test_cloud_egress_gate` |
| **Logging Data Spill** | Log files leak confidential document text, case names, or OCR dumps to system log aggregators. | Unprotected plaintext corpus data written to disk logs. | • Structured logs capture chunk counts, hashes, durations, and error classes only.<br>• Document text, chunk bodies, and OCR dumps are strictly excluded from logging. | `test_zero_chunk_text_in_logs` |

---

## 2. What "Air-Gap" Does NOT Cover

Marketing often uses "air-gapped" as an all-encompassing security guarantee. In an operational homelab or private datacenter, **KruschNexus guarantees zero runtime network calls from its ingestion and retrieval engine**. However, operators must recognize what falls outside this application-layer boundary:

### 1. Ollama Model Supply Chain & Weights
* **Non-goal**: KruschNexus communicates with Ollama via local HTTP (`http://127.0.0.1:11434/api/embed`).
* **Operator Responsibility**: Downloading weights (`ollama pull bge-large`) requires outbound internet access at setup time. Operators requiring strict air-gap must verify model file SHA-256 hashes and mirror weights via local private registries or sneaker-net USB drives.

### 2. Base Docker Image & Package Registries
* **Non-goal**: The Dockerfile builds on standard Debian/Ubuntu base images and Python PyPI packages.
* **Operator Responsibility**: Building containers requires package downloads unless built within an internal CI pipeline with vetted local package mirrors.

### 3. PostgreSQL Disk Backups & Volume Snapshots
* **Non-goal**: KruschNexus stores relational chunks and vectors in PostgreSQL with Row Level Security.
* **Operator Responsibility**: Database backup dumps (`pg_dump`) and filesystem volume snapshots are not encrypted by KruschNexus. Host operators must configure LUKS disk encryption, PostgreSQL transparent data encryption, and restrict backup directory permissions (`chmod 700`).

### 4. Client Terminal & MCP Host Environment
* **Non-goal**: KruschNexus provides an MCP server (`nexus-mcp`). If the host machine running the MCP client (Cursor, Claude Desktop, Antigravity) is compromised, the attacker can execute authorized MCP tools.
* **Operator Responsibility**: Ensure endpoint security on developer workstations connecting to the MCP socket or SSE endpoint.

### 5. Local Physical Machine Access & Memory Dumps
* **Non-goal**: KruschNexus does not protect against physical memory acquisition (cold boot attacks) or root-level memory scrapers on the homelab host.
* **Operator Responsibility**: Restrict physical and SSH access to homelab server nodes.

---

## 3. Threat Matrix by Deployment Environment

| Environmental Characteristic | Dev / Localhost Profile | Homelab LAN Profile | Production Private-Cloud Profile |
| :--- | :--- | :--- | :--- |
| **API Token (`NEXUS_API_TOKEN`)** | Optional (defaults to unrestricted) | Required | Mandatory (strictly enforced) |
| **Network Interface** | `127.0.0.1` | `127.0.0.1` (reverse-proxied via WireGuard/Tailscale) | Dedicated private VLAN |
| **Allowed Roots** | Current repo / temp dirs | Explicit watch directories | Strict mount points (`/data/nexus`) |
| **Database RLS** | SQLite in-memory / file | PostgreSQL + RLS | PostgreSQL + RLS + separate tenant roles |
| **Doctor Audit** | `nexus doctor` (advisory) | `nexus doctor` (strict check) | `nexus doctor --json --profile=prod` in CI gate |
