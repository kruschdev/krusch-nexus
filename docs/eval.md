# KruschNexus Evaluation Suite & Honesty Report

KruschNexus evaluation separates frozen fixture invariant locks from genuine generalization, adversarial resilience, and multi-tenant property guarantees.

---

## 1. Evaluation Suites & Release Gates

The evaluation harness is partitioned into five distinct suites in `tests/eval/`:

| Suite | File | Purpose | Release Gate Requirement | Measured Result |
|---|---|---|---|---|
| **`eval_heldout`** | [`test_eval_heldout.py`](../tests/eval/test_eval_heldout.py) | **Primary Release Gate**: Evaluates 26 queries across 7 parser families (`digital_pdf`, `ocr_pdf`, `docx`, `html_email`, `tabular_csv`, `statutory_txt`, `mixed_pdf`) on messy documents (SEC 10-K, two-column newspapers, 150 DPI scans, redacted orders). | **Citation Accuracy $\ge 80.0\%$**;<br>**Span Precision $\ge 80.0\%$**;<br>Recall@5 $\ge 85.0\%$ | **Citation Acc = 100.0%**<br>**Span Precision = 100.0%**<br>**Recall@5 = 100.0%** |
| **`eval_hard_negatives`** | [`test_eval_hard_negatives.py`](../tests/eval/test_eval_hard_negatives.py) | Non-statutory hard negatives: queries composed exclusively of synonyms without `§` or `Section` tokens. Prevents section boost score inflation. | **`section_boost == False`**;<br>Zero false positive cross-leakage | **100.0% Precision**<br>0.0% False Positives |
| **`eval_regression`** | [`test_eval_regression.py`](../tests/eval/test_eval_regression.py) | Invariant regression lock over frozen baseline instruments. Ensures refactors to chunkers, parsers, or ranking preserve baseline invariants. | **Recall@5 = 100.0%**;<br>Citation Accuracy $\ge 80.0\%$ | **Recall@5 = 100.0%**<br>Citation Acc = 98.3%<br>MRR = 0.989 |
| **`eval_adversarial`** | [`test_eval_adversarial.py`](../tests/eval/test_eval_adversarial.py) | Stress-tests against difficult document structures: two-column statutory PDFs, redline draft PDFs with strikethroughs, degraded fax scans with stamps, blank scans, and encrypted PDFs. | **Zero unhandled exceptions**; Fail-closed on encrypted PDFs; OCR CER $\le 45\%$ | **0 exceptions**; Fail-closed verified; CER/WER benchmark passed |
| **`eval_isolation`** | [`test_eval_isolation.py`](../tests/eval/test_eval_isolation.py) | Property test for tenant isolation. Executes 75 cross-tenant probes across separate tenant workspaces with overlapping vocabulary and keys. | **0.0000% cross-workspace leakage** | **0.0000% leakage** (0/75 probes) |

---

## 2. Held-Out Benchmark Results Across 7 Parser Families

Evaluated using `pytest tests/eval -m heldout -s`:

```
======================================================================
  📊 EVAL_HELDOUT COMPREHENSIVE BENCHMARK RESULTS
======================================================================
Total Held-Out Queries:    26
Citation Accuracy (PRI):   100.0% (26/26)
Span Precision (PRI):      100.0% (26/26)
Recall@5 (SEC):            100.0% (26/26)
----------------------------------------------------------------------
Parser Family    | Count  | Citation Acc | Span Prec  | Recall@5
----------------------------------------------------------------------
digital_pdf      | 3      | 100.0%       | 100.0%     | 100.0%  
docx             | 1      | 100.0%       | 100.0%     | 100.0%  
html_email       | 1      | 100.0%       | 100.0%     | 100.0%  
mixed_pdf        | 2      | 100.0%       | 100.0%     | 100.0%  
ocr_pdf          | 4      | 100.0%       | 100.0%     | 100.0%  
statutory_txt    | 14     | 100.0%       | 100.0%     | 100.0%  
tabular_csv      | 1      | 100.0%       | 100.0%     | 100.0%  
======================================================================
```

---

## 3. How to Reproduce the Eval Table with Exact Fixture Hashes

Every test fixture is cryptographically signed in [`tests/fixtures/fixtures_manifest.json`](../tests/fixtures/fixtures_manifest.json).

### Step 1: Verify Fixture Integrity
Run the manifest integrity test:
```bash
pytest tests/eval/test_eval_heldout.py -k "test_heldout_fixtures_sha256_manifest"
```
Or check individual fixture hashes directly:
```bash
sha256sum tests/fixtures/heldout_sec_10k_table.pdf
# Expected: 2bc04ce8356eeae4637eaef1ee8618bb989689622d0be15dd7e63b655f48f4e6

sha256sum tests/fixtures/heldout_twocolumn_newspaper.pdf
# Expected: 0b5d568c078021d6365a6f2382103328eb9b251fc4ecbbdd011fa9b80b72a6b2

sha256sum tests/fixtures/heldout_medical_scan_150dpi.pdf
# Expected: ecd90225a0ead24a69ce594ff6408e57ee2136abfc2e96bd750c68d5de7e4141

sha256sum tests/fixtures/heldout_redacted_order.pdf
# Expected: dbe83beff749f7a731efc2c31cbaec31bcf25c862bc7b0a7ae126c04fdf80757

sha256sum tests/fixtures/heldout_mixed_digital_scan.pdf
# Expected: 49aa8aa8b3941a3194a37651df9a34bc44bf77271816bc8d8f7800e28f306d70
```

### Step 2: Run the One-Command Release Gate
```bash
pytest tests/eval -m heldout -s
```

### Step 3: Run the Non-Statutory Hard-Negative Set
```bash
pytest tests/eval/test_eval_hard_negatives.py -s
```

---

## 4. Decoupled Scoring Rules & Methodology

KruschNexus strictly distinguishes between text retrieval, citation precision, and character span fidelity.

### Metric 1: Recall@5
$$\text{Recall@5} = \frac{\text{Queries where the ground-truth document is in top 5}}{\text{Total Queries}}$$
Measures whether the hybrid RRF engine surfaced the correct source document within the first 5 results.

### Metric 2: Citation Accuracy (Exact-Match)
$$\text{Citation Accuracy} = \frac{\text{Queries where Top-1 Hit has the exact physical page / locator}}{\text{Total Queries}}$$
- **Paged Documents (PDF)**: Top hit must point to the **exact physical 1-based page** (`pdf_page`). If a snippet contains the matching words but points to Page 1 instead of Page 2, **Citation Accuracy is scored as 0 (FAIL)** even if Recall@5 succeeds.
- **Unpaged Documents (DOCX, EML, CSV, HTML)**: Top hit must preserve `page_number = None` (never fabricate "Page 1") and must match the structural heading locator (`locator` or `header`).

### Metric 3: Span Precision (Character Bounds)
$$\text{Span Precision} = \frac{\text{Queries where Top-1 Hit provides character bounds containing the matched snippet}}{\text{Total Queries}}$$
Verifies that `char_start` and `char_end` are non-null, valid offsets, and accurately bound the target text span in the underlying document.

### Metric 4: OCR Character Error Rate (CER) and Word Error Rate (WER)
$$\text{CER} = \frac{\text{LevenshteinDistance}(\text{reference\_chars}, \text{hypothesis\_chars})}{|\text{reference\_chars}|}$$
$$\text{WER} = \frac{\text{LevenshteinDistance}(\text{reference\_words}, \text{hypothesis\_words})}{|\text{reference\_words}|}$$
Computed directly on scanned test pages against human ground-truth transcripts.

---

## 5. Machine-Readable Evaluation Reports

Run the evaluation generator to produce `eval_report.json`:

```bash
# Generate eval_report.json
python -m krusch_nexus.eval_report

# Inspect evaluation output
cat eval_report.json
```

CI automatically verifies that all release gate requirements in `eval_report.json` maintain `status: "PASS"`.
