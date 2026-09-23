# KruschNexus Evaluation Suite & Honesty Report

KruschNexus evaluation separates frozen fixture invariant locks from genuine generalization, adversarial resilience, and multi-tenant property guarantees.

---

## 1. Evaluation Suites & Release Gates

The evaluation harness is partitioned into four distinct suites in `tests/eval/`:

| Suite | File | Purpose | Release Gate Requirement | Measured Result |
|---|---|---|---|---|
| **`eval_regression`** | [`test_eval_regression.py`](file:///home/krusch/homelab/projects/krusch-nexus/tests/eval/test_eval_regression.py) | Invariant regression lock over 6 frozen fixtures and 60 gold queries. Ensures that refactors to chunkers, parsers, or ranking do not break established baseline invariants. | **Recall@5 = 100.0%** (regression fail if dropped); Citation Accuracy $\ge 80.0\%$ | **Recall@5 = 100.0%**<br>Citation Acc = 98.3%<br>MRR = 0.989 |
| **`eval_heldout`** | [`test_eval_heldout.py`](file:///home/krusch/homelab/projects/krusch-nexus/tests/eval/test_eval_heldout.py) | Evaluates 25 unseen queries against 5 legal instruments *not* used during chunker/boost tuning (bylaws, promissory note, employment agreement, lease amendment, software license). | **Recall@5 $\ge 85.0\%$**;<br>Citation Accuracy $\ge 80.0\%$;<br>Span Precision $\ge 80.0\%$ | **Recall@5 = 100.0%**<br>Citation Acc = 96.0%<br>Span Precision = 96.0% |
| **`eval_adversarial`** | [`test_eval_adversarial.py`](file:///home/krusch/homelab/projects/krusch-nexus/tests/eval/test_eval_adversarial.py) | Stress-tests against real difficult document structures: two-column statutory PDFs, redline draft PDFs with strikethroughs, degraded fax scans with FILED stamps, blank scans, and encrypted PDFs. | **Zero unhandled exceptions**; Fail-closed on encrypted PDFs; OCR CER $\le 45\%$ | **0 exceptions**; Fail-closed verified; CER/WER benchmark passed |
| **`eval_isolation`** | [`test_eval_isolation.py`](file:///home/krusch/homelab/projects/krusch-nexus/tests/eval/test_eval_isolation.py) | Property test for tenant isolation. Executes 75 cross-tenant probes across 5 separate tenant workspaces with overlapping vocabulary and secret keys. | **0.0000% cross-workspace leakage** | **0.0000% leakage** (0/75 probes) |

---

## 2. Decoupled Scoring Rules & Methodology

KruschNexus strictly distinguishes between text retrieval, citation precision, and character span fidelity.

### Metric 1: Recall@5
$$\text{Recall@5} = \frac{\text{Queries where the ground-truth document is in top 5}}{\text{Total Queries}}$$
Measures whether the hybrid RRF engine surfaced the correct source document within the first 5 results.

### Metric 2: Citation Accuracy (Exact-Match)
$$\text{Citation Accuracy} = \frac{\text{Queries where Top-1 Hit has the exact physical page / locator}}{\text{Total Queries}}$$
- **Paged Documents (PDF)**: Top hit must point to the **exact physical 1-based page**. If a snippet contains the matching words but points to Page 1 instead of Page 2, **Citation Accuracy is scored as 0 (FAIL)** even if Recall@5 succeeds.
- **Unpaged Documents (DOCX, EML, CSV, HTML)**: Top hit must preserve `page_number = None` (never fabricate "Page 1") and must match the structural heading locator (`locator` or `header`).

### Metric 3: Span Precision (Character Bounds)
$$\text{Span Precision} = \frac{\text{Queries where Top-1 Hit provides character bounds containing the matched snippet}}{\text{Total Queries}}$$
Verifies that `char_start` and `char_end` are non-null, valid offsets, and accurately bound the target text span in the underlying document.

### Metric 4: OCR Character Error Rate (CER) and Word Error Rate (WER)
$$\text{CER} = \frac{\text{LevenshteinDistance}(\text{reference\_chars}, \text{hypothesis\_chars})}{|\text{reference\_chars}|}$$
$$\text{WER} = \frac{\text{LevenshteinDistance}(\text{reference\_words}, \text{hypothesis\_words})}{|\text{reference\_words}|}$$
Computed directly on scanned test pages against human ground-truth transcripts.

---

## 3. OCR Benchmark Specification & Measured Error Rates

Rather than making ungrounded claims of "100% OCR recall", KruschNexus benchmarks optical character recognition on actual scanned documents:

1. **High-Contrast Clean Scan (`scanned_page.pdf` p.1)**:
   - Reference: Settlement Release Exhibit B with liquidated damages clause.
   - Measured Confidence: **0.74**
   - Measured CER: **7.39%**
   - Measured WER: **39.29%**
   - Status: **PASS** (Gate: CER $\le 15.0\%$, WER $\le 45.0\%$)

2. **Degraded Fax Scan with Noise & Stamp (`adversarial_fax_stamp.pdf` p.1)**:
   - Reference: Settlement Release with transmission header and red "RECEIVED & FILED" stamp overlay.
   - Measured Confidence: **0.64**
   - Measured CER: **38.69%**
   - Measured WER: **55.00%**
   - Status: **PASS** (Gate: CER $\le 45.0\%$, WER $\le 65.0\%$)

---

## 4. Machine-Readable Evaluation Reports

Run the evaluation generator to produce `eval_report.json`:

```bash
# Generate eval_report.json
python -m krusch_nexus.eval_report

# Inspect evaluation output
cat eval_report.json
```

CI automatically verifies that all release gate requirements in `eval_report.json` maintain `status: "PASS"`.
