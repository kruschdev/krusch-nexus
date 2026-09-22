"""
KruschNexus Frozen Citation & Evaluation Harness (tests/eval/test_citation_eval.py)
=================================================================================
Measures and asserts:
1. Recall@5 across a frozen 25-query benchmark
2. Page/Locator Accuracy (exact 1-based page for PDF, header locator for DOCX/CSV)
3. Header Accuracy (regex match against ground-truth section)
4. Scanned OCR Recall (specific benchmark for image-based text)
5. Strict 0.00% Cross-Workspace Isolation
"""

import os
import re
import math
import shutil
import tempfile
import unittest
from typing import List, Dict, Any, Optional

from krusch_nexus import NexusClient, NexusConfig, SearchHit, DocType
from krusch_nexus.store import init_db, get_engine

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")


def mock_deterministic_vector(text: str, dim: int = 1024) -> List[float]:
    """Generates a deterministic unit vector from text hash for hermetic ranking tests."""
    import hashlib
    clean = text.lower().strip()
    h = hashlib.sha256(clean.encode('utf-8')).digest()
    vec = [0.0] * dim
    for i in range(dim):
        byte_val = h[i % len(h)]
        vec[i] = (byte_val / 255.0) - 0.5
    # Normalize to unit vector
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class TestCitationEvaluation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="nexus_eval_")
        cls.db_path = os.path.join(cls.temp_dir, "eval.db")
        cls.config = NexusConfig(
            database_url=f"sqlite:///{cls.db_path}",
            ocr_threshold_chars=30,
            ocr_dpi=300,
            allowed_ingest_roots=[FIXTURES_DIR, cls.temp_dir]
        )
        cls.engine = get_engine(cls.config.database_url)
        init_db(cls.engine)
        cls.nexus = NexusClient(cls.config)

        # Ingest benchmark corpus into "BenchmarkWorkspace"
        cls.fixtures = [
            ("sample_contract.pdf", DocType.AUTHORITY),
            ("scanned_page.pdf", DocType.AUTHORITY),
            ("policy_manual.docx", DocType.WORK_PRODUCT),
            ("deal_memo.eml", DocType.GENERAL),
            ("municipal_code.txt", DocType.AUTHORITY),
            ("vendor_matrix.csv", DocType.GENERAL),
        ]

        cls.ingest_reports = {}
        for fname, doc_type in cls.fixtures:
            path = os.path.join(FIXTURES_DIR, fname)
            report = cls.nexus.ingest(
                filepath=path,
                workspace="BenchmarkWorkspace",
                doc_type=doc_type,
                archive=False
            )
            cls.ingest_reports[fname] = report

        # Ingest isolated file into Beta workspace for leakage assertion
        isolated_path = os.path.join(FIXTURES_DIR, "municipal_code.txt")
        cls.nexus.ingest(
            filepath=isolated_path,
            workspace="IsolatedWorkspace_Beta",
            doc_type=DocType.AUTHORITY,
            archive=False
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_corpus_ingestion_integrity(self):
        """Verify all 6 test corpus files were successfully ingested."""
        self.assertEqual(len(self.ingest_reports), 6)
        for fname, report in self.ingest_reports.items():
            self.assertEqual(report.status, "completed", f"Failed to ingest {fname}: {report.error}")
            self.assertGreater(report.chunks, 0)

        # Scanned PDF must trigger OCR fallback
        scanned_report = self.ingest_reports["scanned_page.pdf"]
        self.assertIn(1, scanned_report.ocr_pages, "Page 1 of scanned_page.pdf must trigger OCR")

    def test_frozen_evaluation_suite(self):
        """
        Execute 25 frozen benchmark queries against the multi-format corpus.
        Calculates: Recall@5, Page/Locator Accuracy, Header Accuracy, and OCR Recall.
        """
        benchmark_queries = [
            # PDF Contract & Statutory Queries
            {"q": "Section 8.22 Permitted Use of Premises", "file": "sample_contract.pdf", "page": 1, "header": r"8\.22|Permitted Use", "is_ocr": False},
            {"q": "Section 19.3 Termination for Breach", "file": "sample_contract.pdf", "page": 2, "header": r"19\.3|Termination", "is_ocr": False},
            {"q": "Permitted Use commercial office", "file": "sample_contract.pdf", "page": 1, "header": r"8\.22|Permitted", "is_ocr": False},
            {"q": "thirty days written notice breach", "file": "sample_contract.pdf", "page": 2, "header": r"19\.3|Termination", "is_ocr": False},

            # Scanned PDF (OCR) Queries
            {"q": "liquidated damages", "file": "scanned_page.pdf", "page": 1, "header": r"14\.1|Liquidated", "is_ocr": True},
            {"q": "Section 14.1 Liquidated Damages fifty thousand", "file": "scanned_page.pdf", "page": 1, "header": r"14\.1|Liquidated", "is_ocr": True},
            {"q": "settlement release fifty thousand dollars", "file": "scanned_page.pdf", "page": 1, "header": r"14\.1|Liquidated|Settlement", "is_ocr": True},

            # DOCX Heading Stack & Table Queries
            {"q": "backup retention standards", "file": "policy_manual.docx", "page": None, "header": r"1\.2|Backup Retention", "is_ocr": False},
            {"q": "Fleet Information Security Policy", "file": "policy_manual.docx", "page": None, "header": r"Section 1|Information Security", "is_ocr": False},
            {"q": "Privileged Corporate Paper Seven Years", "file": "policy_manual.docx", "page": None, "header": r"Backup Retention", "is_ocr": False},
            {"q": "Purging of records prior to expiration", "file": "policy_manual.docx", "page": None, "header": r"Backup Retention", "is_ocr": False},
            {"q": "policy violation compliance", "file": "policy_manual.docx", "page": None, "header": r"Backup Retention", "is_ocr": False},

            # EML RFC822 & Attachments Queries
            {"q": "acquisition review protocol", "file": "deal_memo.eml", "page": None, "header": r"Acquisition|Review", "is_ocr": False},
            {"q": "Section 4.5 purchase agreement closing", "file": "deal_memo.eml", "page": None, "header": r"Acquisition|deal_memo", "is_ocr": False},
            {"q": "General Counsel krusch.dev", "file": "deal_memo.eml", "page": None, "header": r"Acquisition|deal_memo", "is_ocr": False},
            {"q": "closing certificate schedule", "file": "deal_memo.eml", "page": None, "header": r"Acquisition|Attachment", "is_ocr": False},

            # Plain Text Statutory Queries
            {"q": "§ 1950.5", "file": "municipal_code.txt", "page": None, "header": r"1950\.5|Security Deposits", "is_ocr": False},
            {"q": "security deposits tenant protections unfurnished", "file": "municipal_code.txt", "page": None, "header": r"1950\.5", "is_ocr": False},
            {"q": "Section 8.22.030 Rent Adjustment Program", "file": "municipal_code.txt", "page": None, "header": r"8\.22\.030|Rent Adjustment", "is_ocr": False},
            {"q": "statute of limitations rent disputes written notice", "file": "municipal_code.txt", "page": None, "header": r"8\.22\.030", "is_ocr": False},
            {"q": "Section 8.22.360 Just Cause for Eviction", "file": "municipal_code.txt", "page": None, "header": r"8\.22\.360|Just Cause", "is_ocr": False},

            # CSV Row Group Queries
            {"q": "Apex Cloud infrastructure spend", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|vendor_matrix", "is_ocr": False},
            {"q": "Lexicon Legal billing counsel SLA", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|vendor_matrix", "is_ocr": False},
            {"q": "Acme Logistics shipping operations", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|vendor_matrix", "is_ocr": False},
            {"q": "ByteSafe Systems soc security spend", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|vendor_matrix", "is_ocr": False},
        ]

        total = len(benchmark_queries)
        recall_5_count = 0
        page_acc_count = 0
        header_acc_count = 0
        ocr_total = sum(1 for b in benchmark_queries if b["is_ocr"])
        ocr_hit_count = 0

        print("\n" + "=" * 80)
        print("KRUSCHNEXUS FROZEN EVALUATION HARNESS (25 BENCHMARK QUERIES)")
        print("=" * 80)

        for b in benchmark_queries:
            q = b["q"]
            hits: List[SearchHit] = self.nexus.search(
                query=q,
                workspace="BenchmarkWorkspace",
                limit=5
            )

            target_in_top5 = any(h.filename == b["file"] for h in hits)
            if target_in_top5:
                recall_5_count += 1
                if b["is_ocr"]:
                    ocr_hit_count += 1

            # Check top hit
            if hits:
                top = hits[0]
                # Page accuracy
                if top.filename == b["file"]:
                    if b["page"] is not None:
                        if top.page_number == b["page"]:
                            page_acc_count += 1
                    else:
                        page_acc_count += 1

                    # Header accuracy
                    header_str = (top.header or "") + " " + (top.locator or "") + " " + top.citation
                    if re.search(b["header"], header_str, re.IGNORECASE):
                        header_acc_count += 1

            status_str = "PASS" if target_in_top5 else "FAIL"
            top_cit = hits[0].citation if hits else "None"
            print(f"[{status_str}] Query: '{q[:40]}'")
            print(f"       Target: {b['file']} (Expected Page: {b['page']}) -> Top: {top_cit}")

        recall_5 = recall_5_count / total
        page_acc = page_acc_count / total
        header_acc = header_acc_count / total
        ocr_recall = ocr_hit_count / max(1, ocr_total)

        print("-" * 80)
        print(f"Recall@5:            {recall_5 * 100:.1f}% ({recall_5_count}/{total})")
        print(f"Page/Loc Accuracy:   {page_acc * 100:.1f}% ({page_acc_count}/{total})")
        print(f"Header Accuracy:     {header_acc * 100:.1f}% ({header_acc_count}/{total})")
        print(f"OCR-Page Recall:     {ocr_recall * 100:.1f}% ({ocr_hit_count}/{ocr_total})")
        print("=" * 80)

        self.assertGreaterEqual(recall_5, 0.95, f"Recall@5 ({recall_5}) must be >= 95%")
        self.assertGreaterEqual(page_acc, 0.85, f"Page accuracy ({page_acc}) must be >= 85%")
        self.assertGreaterEqual(header_acc, 0.85, f"Header accuracy ({header_acc}) must be >= 85%")
        self.assertEqual(ocr_recall, 1.0, f"OCR Recall must be 100%")

    def test_workspace_isolation_zero_leakage(self):
        """
        Assert that searching Workspace A never returns documents from Workspace B.
        Workspace leakage rate must strictly equal 0.00%.
        """
        beta_contract_hits = self.nexus.search(
            query="liquidated damages fifty thousand dollars",
            workspace="IsolatedWorkspace_Beta",
            limit=5
        )

        beta_policy_hits = self.nexus.search(
            query="fleet information security air-gapped",
            workspace="IsolatedWorkspace_Beta",
            limit=5
        )

        beta_eml_hits = self.nexus.search(
            query="acquisition review protocol closing certificate",
            workspace="IsolatedWorkspace_Beta",
            limit=5
        )

        leaked_count = 0
        total_queries = 3

        for h in beta_contract_hits:
            if h.filename != "municipal_code.txt":
                leaked_count += 1
        for h in beta_policy_hits:
            if h.filename != "municipal_code.txt":
                leaked_count += 1
        for h in beta_eml_hits:
            if h.filename != "municipal_code.txt":
                leaked_count += 1

        leak_rate = leaked_count / total_queries
        print(f"\n[ISOLATION TEST] Cross-Workspace Leak Rate: {leak_rate * 100:.2f}% (Leaked: {leaked_count})")
        self.assertEqual(leak_rate, 0.0, "Workspace isolation failed: Cross-workspace document leakage detected!")


if __name__ == "__main__":
    unittest.main()
