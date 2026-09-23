# KruschNexus Evaluation Suite & Honesty Report

KruschNexus evaluation separates frozen fixture invariant locks from genuine generalization, adversarial resilience, and multi-tenant property guarantees.

---

## 1. Evaluation Suites

The evaluation harness is partitioned into four distinct suites in `tests/eval/`:

| Suite | File | Purpose | Release Gate |
|---|---|---|---|
| **`eval_regression`** | [`test_eval_regression.py`](file:///home/krusch/homelab/projects/krusch-nexus/tests/eval/test_eval_regression.py) | Invariant regression lock over 6 frozen fixtures and 60 gold queries. Ensures that refactors to chunkers, parsers, or ranking do not break established baseline invariants. | **Recall@5 = 100.0%** (regression fail if dropped); Citation Accuracy $\ge 80.0\%$ |
| **`eval_heldout`** | [`test_eval_heldout.py`](file:///home/krusch/homelab/projects/krusch-nexus/tests/eval/test_eval_heldout.py) | Evaluates documents *not* used during chunker/boost tuning (e.g. unseen corporate bylaws and secured promissory notes). Tests generalization to novel contract layouts. | **Recall@5 $\ge 85.0\%$**; Citation Accuracy $\ge 80.0\%$ |
| **`eval_adversarial`** | [`test_eval_adversarial.py`](file:///home/krusch/homelab/projects/krusch-nexus/tests/eval/test_eval_adversarial.py) | Stress-tests against difficult document structures: two-column statutes, redlined drafts with strikethroughs, blank scans, running headers/footers, and encrypted PDFs. | **Zero unhandled exceptions**; Fail-closed on encrypted PDFs |
| **`eval_isolation`** | [`test_eval_isolation.py`](file:///home/krusch/homelab/projects/krusch-nexus/tests/eval/test_eval_isolation.py) | Property test for tenant isolation. Executes cross-queries across 5 separate tenant workspaces with overlapping vocabulary and secret keys. | **0.00% cross-workspace leakage** |

---

## 2. Decoupled Scoring Rules & Methodology

KruschNexus strictly distinguishes between text retrieval and citation precision.

### Metric 1: Recall@5
$$\text{Recall@5} = \frac{\text{Queries where the ground-truth document is in top 5}}{\text{Total Queries}}$$
Measures whether the hybrid RRF engine surfaced the correct source document within the first 5 results.

### Metric 2: Citation Accuracy (Exact-Match)
$$\text{Citation Accuracy} = \frac{\text{Queries where Top-1 Hit has the exact physical page / locator}}{\text{Total Queries}}$$
- **Paged Documents (PDF)**: Top hit must point to the **exact physical 1-based page**. If a snippet contains the matching words but points to Page 1 instead of Page 2, **Citation Accuracy is scored as 0 (FAIL)** even if Recall@5 succeeds.
- **Unpaged Documents (DOCX, EML, CSV, HTML)**: Top hit must preserve `page_number = None` (never fabricate "Page 1") and must match the structural heading locator (`locator` or `header`).

### Metric 3: MRR (Mean Reciprocal Rank)
$$\text{MRR} = \frac{1}{|Q|} \sum_{i=1}^{|Q|} \frac{1}{\text{rank}_i}$$
Measures the ranking position of the first correct hit.

---

## 3. The 8-Page OCR Benchmark Specification

Rather than making ungrounded claims of "100% OCR recall", KruschNexus benchmarks optical character recognition on a specific 8-page corpus containing mixed and difficult scans:

1. **Page 1: Settlement Release Exhibit B (`scanned_page.pdf`)** — 300 DPI high-contrast legal release with liquidated damages clauses.
2. **Page 2: Mixed Vector/Scan Page 3 (`mixed_sample.pdf`)** — Scanned exhibit page appended after clean vector pages, testing selective OCR triggering.
3. **Page 3: Two-Column Commercial Code (`adversarial_twocolumn.txt`)** — Multi-column statutory text testing layout preservation.
4. **Page 4: Blank Scan Page (`adversarial_blank_scan.pdf`)** — Empty white page verifying `OCR_EMPTY_PAGE` warning generation without crashing.
5. **Page 5: Dense Table Grid (`vendor_matrix.csv`)** — Tabular financial row chunks with replayed headers.
6. **Page 6: Redlined Draft Contract (`adversarial_redline.txt`)** — Strikethrough and bracketed additions testing OCR token fidelity.
7. **Page 7: Paged Multi-Clause Lease (`sample_contract.pdf` p. 1)** — Digital text verified for non-triggering of OCR.
8. **Page 8: Breach Remedies Notice (`sample_contract.pdf` p. 2)** — Digital vector text verified for non-overwriting.

---

## 4. Running the Complete Evaluation

```bash
# Run regression suite
pytest tests/eval/test_eval_regression.py -v -s

# Run held-out generalization suite
pytest tests/eval/test_eval_heldout.py -v -s

# Run adversarial edge-case suite
pytest tests/eval/test_eval_adversarial.py -v -s

# Run multi-tenant isolation property test
pytest tests/eval/test_eval_isolation.py -v -s

# Run all eval suites together
pytest tests/eval/ -v -s
```
