# Changelog

All notable changes to the KruschNexus project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-09-22

### Added
- **Core Corpus Factory Focus**: Decoupled ingestion spine (`krusch_nexus`) from external agent OS / platform cruft.
- **Top-Level Package (`krusch_nexus`)**: Public Python library supporting `from krusch_nexus import Nexus`.
- **Typed Pydantic Models**: Added `IngestReport`, `ChunkHit`, and `Citation` with canonical `{filename} p.{n} § {header}` formatter.
- **Air-Gap Compile & Startup Checks**: Refuse startup if `EMBEDDING_PROVIDER != "ollama"` without `ALLOW_CLOUD=1`.
- **Path Sandboxing**: Resolve paths with `Path.resolve()` against allowlisted ingest roots; block access to sensitive system paths (`/etc`, `/proc`, etc.).
- **Parser Hardening**:
  - Emits DOCX tables in natural document order between surrounding paragraphs.
  - PDF image XObject presence check to bypass redundant OCR on blank pages.
  - Pinned Tesseract OCR with `--psm 3`, `-l eng`, and 30s subprocess timeout.
  - Python stdlib `HTMLParser` replacing nested regular expressions.
  - RFC2047 MIME encoded-word header decoding for EML files.
- **Database & Isolation Enhancements**:
  - Refuse connections using insecure default password (`kruschpassword`).
  - Added `ingested_at`, `ocr_pages`, `status`, `embedding_model`, and `embedding_dim` to `Document`.
  - Required workspace isolation on every search query.
- **Evaluation Benchmark (`tests/eval/`)**:
  - Recall@5 testing on fixed multi-format legal/business corpus.
  - Citation format validation.
  - Assertion of 0.00% cross-workspace leakage.
- **Offline Self-Check**: Added `nexus verify --offline` command asserting zero outbound egress.
- **Hygiene & Packaging**: Unified `pyproject.toml`, MIT `LICENSE`, `Makefile`, and backward compatibility facade in `src/backend/`.
