# Contributing to KruschNexus

KruschNexus is an air-gapped, citation-preserving ingestion and search engine built around one core invariant: **format-honest, page-faithful locators**.

Before contributing, please read and follow these architectural boundaries.

---

## 1. Core Principles & Non-Negotiables

1. **Air-Gap Posture**: Strictly zero cloud LLMs, zero external API telemetry, and zero remote network requests during ingestion or retrieval. All parsing, OCR, chunking, and embedding execution must run on local infrastructure.
2. **Format-Honest Citations**: Never emit `p. None` or fabricated page numbers. Paged documents (PDF, DOCX) must track 1-indexed physical pages. Non-paged documents (HTML, Markdown, TXT) must use headings or structural locators.
3. **Tenant & Workspace Isolation**: All database queries must enforce workspace boundary checks (`WHERE workspace_id = :ws_id`). Cross-tenant leakage is a P0 regression.
4. **No Domain Law in Core**: KruschNexus is a general citation and retrieval engine. Do not hardcode domain-specific legal heuristics (e.g., California-specific statutory interpretations or custom lease clauses) into `krusch_nexus/`. Domain reasoning belongs in higher-level client applications or specialized toolchains.
5. **Frozen Public Contracts**: `NexusClient` is the public API entrypoint (`Nexus` as alias). Do not add ad-hoc parameters or breaking field renames to `SearchHit`, `StructuredLocator`, or `IngestReport`.

---

## 2. How to Add a Parser

Parsers live in `src/krusch_nexus/parsers/` and must adhere to the `BaseParser` interface:

1. Implement `parse(filepath: str, doc_type: DocType = DocType.GENERAL) -> ParserResult`.
2. Extract text on a **per-page basis** using `PageData(page_number=i, text=..., extra={...})`.
3. If the file format lacks physical pages (e.g., CSV, Markdown, plain text), emit `PageData(page_number=None, ...)`.
4. Capture OCR and image provenance:
   - If fallback OCR is triggered, record page confidence scores.
   - If confidence is below the threshold floor (`0.60`), save a quarantined page image to `~/.cache/krusch_nexus/quarantine/` and set `PageData.extra["page_image_path"]`.
   - Preserve redlines/tracked changes in `PageData.extra["redline_changes"]`.
5. Register the parser in `src/krusch_nexus/parsers/registry.py` and associate it with its target MIME types.
6. Add unit tests in `tests/unit/test_parsers.py` with both clean and adversarial fixtures.

---

## 3. How to Add a Gold Query to Eval

Evaluations measure locator accuracy and retrieval recall against a fixed test corpus:

1. Place the fixture file in `tests/fixtures/`.
2. Update `tests/fixtures/create_fixtures.py` and run it to refresh `tests/fixtures/fixtures_manifest.json` with the new file's SHA-256 hash and byte size.
3. If live Ollama is unavailable during CI, record the fixture embeddings into `tests/fixtures/fixture_embeddings.json`.
4. Add the evaluation query to the evaluation suite (`tests/eval/`):
   - Specify `query`, `target_document`, `expected_page`, and `expected_locator`.
   - Ensure the query evaluates realistic user queries (including statutory citations or section headers).
5. Run the evaluation suite:
   ```bash
   pytest tests/eval/
   ```
   Verify that Wilson score 95% confidence intervals are reported and that `eval_report.json` passes.

---

## 4. Testing & CI Standards

- **Unit Tests (`tests/unit/`)**: Fast, self-contained tests running on in-memory SQLite without external daemons. Must execute in under 10 seconds.
- **Integration Tests (`tests/integration/`)**: Test multi-tenant isolation, FastMCP tool invocations, and database transactions against real PostgreSQL 16 + pgvector.
- **CI Contract Guard**: CI runs `tests/unit/test_api_contract.py` which fails if version strings, README badges, OpenAPI schemas, or FastMCP tool lists drift.
- **Homelab Path Sanitization**: Never commit absolute local filesystem paths (e.g., `/home/...`). Keep all documentation links and test fixtures relative to the repository root.

---

## 5. Development Workflow

```bash
# 1. Install editable package with dev dependencies
pip install -e ".[dev]"

# 2. Run unit tests
pytest tests/unit/

# 3. Check for forbidden cloud dependencies or path leaks
! grep -rn --exclude-dir=__pycache__ --exclude-dir=.git -i -E "openai|anthropic|google\.generativeai" src/
! grep -rn --exclude-dir=__pycache__ --exclude-dir=.git "/home/" src/ docs/ tests/

# 4. Verify doctor diagnostics
nexus doctor --profile prod
```
