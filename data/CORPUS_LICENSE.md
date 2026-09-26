# 📜 KruschNexus Evaluation Corpus License & Data Provenance

> **Authoritative Compliance Statement**: This document defines the legal provenance, copyright status, and usage licensing for all document corpora, benchmark fixtures, and demo databases bundled within KruschNexus.

---

## 1. Provenance Classifications

All document texts in `data/`, `tests/eval/`, and `data/demo.db` belong to strictly documented, non-confidential categories:

### Category A: Public Domain Statutory & Regulatory Authorities
- **Source**: State and municipal public statutory codes, municipal rent ordinances, and federal judicial filings.
- **Legal Status**: Public government works and legislative records, free of proprietary secrecy or copyright restrictions.
- **Fair Use & Reproduction**: Evaluated and reproduced under 17 U.S.C. § 107 for automated research, software benchmarking, citation indexing, and hybrid retrieval evaluation.

### Category B: SEC EDGAR Public Domain Filings (Exhibits 10)
- **Source**: U.S. Securities and Exchange Commission (SEC) EDGAR public corporate disclosures filed pursuant to 17 CFR § 229.601 (Item 601 - Exhibits, specifically Exhibit 10 material contracts).
- **Redaction Policy**: All individual personal names, private residential addresses, phone numbers, and bank account numbers have been redacted or replaced with synthetic placeholders.

### Category C: Synthetic Adversarial Fixtures
- **Source**: Synthetically authored by the Krusch engineering team to rigorously stress-test:
  - Complex hierarchical heading trees (Title > Chapter > Article > Section > Subsection).
  - Malformed boundaries, OCR artifacts, empty pages, and poison pill sequences.
  - Multi-tenant workspace isolation and strict tenant separation.
  - Legal hold preservation and immutable append-only audit ledgers.
- **Licensing**: Released under the **Creative Commons Attribution 4.0 International (CC-BY-4.0)** license and dual-licensed under the **MIT License**.

---

## 2. Zero Confidential Client Data Warranty

The authors of KruschNexus warrant that:
1. **Zero Client Data**: No confidential, attorney-client privileged, non-public personal information (NPI), or sensitive commercial trade secrets are included in this repository.
2. **Deterministic Reproducibility**: Third-party auditors, compliance officers, and open-source contributors can freely clone, execute, inspect, and benchmark the pipeline without exposure to proprietary risk.

---

## 3. Bundled SQLite Demo Fixture (`data/demo.db`)

The pre-seeded SQLite database file (`data/demo.db`) provides an instant, zero-dependency runtime substrate (<0.05s startup):
- `LegalCorpus`: Pre-indexed commercial MSA, municipal rent ordinance, and defense memorandum.
- `LitigationHold`: Evidentiary preservation order under active legal hold (`is_legal_hold = True`).
- Deterministic 1024-dimension unit vector embeddings enabling hybrid search without external Ollama host.
- Immutable `OperatorAudit` ledger records tracking all administrative actions.
