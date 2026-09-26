"""
tests/eval/test_eval_regression.py
==================================
eval_regression: Invariant regression lock on the frozen 60-query benchmark.
Must maintain 100% Recall@5 on known fixtures; any drop indicates an invariant regression.
Reports Recall@5, Citation Accuracy, and MRR separately.
"""

import os
import shutil
import tempfile
import unittest
from typing import List, Optional

from krusch_nexus import NexusClient, NexusConfig, DocType
from krusch_nexus.store import init_db, get_engine

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")


class TestEvalRegression(unittest.TestCase):
    """
    eval_regression suite:
    Verifies that changes to chunking, ranking, or parsers do not break established invariants.
    """

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="nexus_eval_regression_")
        cls.db_path = os.path.join(cls.temp_dir, "regression.db")
        cls.config = NexusConfig(
            database_url=f"sqlite:///{cls.db_path}",
            ocr_threshold_chars=40,
            ocr_dpi=300,
            allowed_ingest_roots=[FIXTURES_DIR, cls.temp_dir]
        )
        cls.engine = get_engine(cls.config.database_url)
        init_db(cls.engine)
        cls.nexus = NexusClient(cls.config)

        # Ingest benchmark corpus into "RegressionWorkspace"
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
                workspace="RegressionWorkspace",
                doc_type=doc_type,
                archive=False
            )
            cls.ingest_reports[fname] = report

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
        self.assertIsNotNone(scanned_report.ocr_mean_confidence, "Mean OCR confidence must be recorded")

    def test_regression_queries(self):
        """
        Execute 60 frozen benchmark queries across the multi-format corpus.
        Calculates:
        - Recall@5 (target doc in top 5)
        - Citation Accuracy (exact physical page for PDF, or section locator for DOCX/CSV/EML)
        - MRR (Mean Reciprocal Rank)
        """
        benchmark_queries = [
            # 1. Vector PDF Contract Queries (sample_contract.pdf)
            {"q": "Section 8.22 Permitted Use of Premises", "file": "sample_contract.pdf", "page": 1, "header": r"8\.22|Permitted Use", "snippet": "Permitted Use", "is_ocr": False},
            {"q": "Section 19.3 Termination for Breach", "file": "sample_contract.pdf", "page": 2, "header": r"19\.3|Termination", "snippet": "Termination for Breach", "is_ocr": False},
            {"q": "Permitted Use commercial office space lease", "file": "sample_contract.pdf", "page": 1, "header": r"8\.22|Permitted", "snippet": "COMMERCIAL LEASE", "is_ocr": False},
            {"q": "thirty days written notice cure breach", "file": "sample_contract.pdf", "page": 2, "header": r"19\.3|Termination", "snippet": "thirty days", "is_ocr": False},
            {"q": "COMMERCIAL LEASE AGREEMENT premises", "file": "sample_contract.pdf", "page": 1, "header": r"8\.22|Permitted", "snippet": "COMMERCIAL LEASE", "is_ocr": False},
            {"q": "Tenant shall cure within thirty days", "file": "sample_contract.pdf", "page": 2, "header": r"19\.3|Termination", "snippet": "thirty days", "is_ocr": False},
            {"q": "Section 8.22", "file": "sample_contract.pdf", "page": 1, "header": r"8\.22", "snippet": "Section 8.22", "is_ocr": False},
            {"q": "Section 19.3", "file": "sample_contract.pdf", "page": 2, "header": r"19\.3", "snippet": "Section 19.3", "is_ocr": False},
            {"q": "lease agreement page 1 permitted use", "file": "sample_contract.pdf", "page": 1, "header": r"8\.22", "snippet": "Section 8.22", "is_ocr": False},
            {"q": "breach of contract cure period tenant", "file": "sample_contract.pdf", "page": 2, "header": r"19\.3", "snippet": "cure within", "is_ocr": False},

            # 2. Scanned Settlement Release Queries (scanned_page.pdf)
            {"q": "EXHIBIT B SCANNED SETTLEMENT RELEASE", "file": "scanned_page.pdf", "page": 1, "header": r"Scanned|EXHIBIT", "snippet": "SETTLEMENT", "is_ocr": True},
            {"q": "Section 14.1 Liquidated Damages fifty thousand dollars", "file": "scanned_page.pdf", "page": 1, "header": r"14\.1|Liquidated", "snippet": "liquidated damages", "is_ocr": True},
            {"q": "liquidated damages fifty thousand dollars", "file": "scanned_page.pdf", "page": 1, "header": r"14\.1|Liquidated", "snippet": "fifty thousand", "is_ocr": True},
            {"q": "twenty-second day of September 2026 executed", "file": "scanned_page.pdf", "page": 1, "header": r"September|2026", "snippet": "September", "is_ocr": True},
            {"q": "settlement release liquidated damages", "file": "scanned_page.pdf", "page": 1, "header": r"Liquidated", "snippet": "damages", "is_ocr": True},
            {"q": "Section 14.1", "file": "scanned_page.pdf", "page": 1, "header": r"14\.1", "snippet": "14.1", "is_ocr": True},
            {"q": "fifty thousand dollars damages", "file": "scanned_page.pdf", "page": 1, "header": r"14\.1", "snippet": "fifty thousand", "is_ocr": True},
            {"q": "September 2026 executed agreement", "file": "scanned_page.pdf", "page": 1, "header": r"September", "snippet": "September", "is_ocr": True},
            {"q": "scanned exhibit b settlement release", "file": "scanned_page.pdf", "page": 1, "header": r"EXHIBIT", "snippet": "SETTLEMENT", "is_ocr": True},
            {"q": "fifty thousand dollars liquidated release", "file": "scanned_page.pdf", "page": 1, "header": r"14\.1", "snippet": "fifty thousand", "is_ocr": True},

            # 3. DOCX Policy Manual Queries (policy_manual.docx)
            {"q": "Fleet Information Security Policy air-gapped", "file": "policy_manual.docx", "page": None, "header": r"Section 1|Security Policy", "snippet": "air-gapped", "is_ocr": False},
            {"q": "Backup Retention Standards schedule", "file": "policy_manual.docx", "page": None, "header": r"Section 1\.2|Retention Standards", "snippet": "schedule", "is_ocr": False},
            {"q": "Privileged Corporate Paper Seven Years retention", "file": "policy_manual.docx", "page": None, "header": r"Section 1\.2|Retention", "snippet": "Seven (7) Years", "is_ocr": False},
            {"q": "Purging of records prior to expiration policy violation", "file": "policy_manual.docx", "page": None, "header": r"Section 1\.2|Retention", "snippet": "policy violation", "is_ocr": False},
            {"q": "homelab nodes air-gapped unauthenticated ingress", "file": "policy_manual.docx", "page": None, "header": r"Section 1|Security Policy", "snippet": "unauthenticated ingress", "is_ocr": False},
            {"q": "Document Class Minimum Retention Period table", "file": "policy_manual.docx", "page": None, "header": r"Section 1\.2|Retention", "snippet": "Minimum Retention Period", "is_ocr": False},
            {"q": "Section 1.2 Backup Retention Standards", "file": "policy_manual.docx", "page": None, "header": r"Section 1\.2", "snippet": "Backup Retention", "is_ocr": False},
            {"q": "Privileged Corporate Paper retention schedule", "file": "policy_manual.docx", "page": None, "header": r"Section 1\.2", "snippet": "Privileged Corporate Paper", "is_ocr": False},
            {"q": "expiration of seven years constitutes policy violation", "file": "policy_manual.docx", "page": None, "header": r"Section 1\.2", "snippet": "seven years", "is_ocr": False},
            {"q": "air-gapped without unauthenticated ingress", "file": "policy_manual.docx", "page": None, "header": r"Section 1", "snippet": "air-gapped", "is_ocr": False},

            # 4. EML Deal Memo Queries (deal_memo.eml)
            {"q": "Privileged Acquisition Review Protocol clearance", "file": "deal_memo.eml", "page": None, "header": r"Acquisition Review|Email", "snippet": "clearance", "is_ocr": False},
            {"q": "Section 4.5 Purchase Agreement regulatory clearance", "file": "deal_memo.eml", "page": None, "header": r"Email", "snippet": "Section 4.5", "is_ocr": False},
            {"q": "General Counsel closing certificate filing deadline", "file": "deal_memo.eml", "page": None, "header": r"Email", "snippet": "closing certificate", "is_ocr": False},
            {"q": "Dear Executive Team review closing certificate", "file": "deal_memo.eml", "page": None, "header": r"Email", "snippet": "Executive Team", "is_ocr": False},
            {"q": "regulatory clearance tomorrow filing deadline", "file": "deal_memo.eml", "page": None, "header": r"Email", "snippet": "filing deadline", "is_ocr": False},
            {"q": "Section 4.5 regulatory clearance protocol", "file": "deal_memo.eml", "page": None, "header": r"Email", "snippet": "Section 4.5", "is_ocr": False},
            {"q": "closing certificate filing deadline tomorrow", "file": "deal_memo.eml", "page": None, "header": r"Email", "snippet": "closing certificate", "is_ocr": False},
            {"q": "General Counsel acquisition protocol memo", "file": "deal_memo.eml", "page": None, "header": r"Email", "snippet": "General Counsel", "is_ocr": False},
            {"q": "Purchase Agreement Section 4.5 clearance", "file": "deal_memo.eml", "page": None, "header": r"Email", "snippet": "Section 4.5", "is_ocr": False},
            {"q": "Privileged deal memo acquisition review", "file": "deal_memo.eml", "page": None, "header": r"Email", "snippet": "Acquisition Review", "is_ocr": False},

            # 5. Municipal Code Queries (municipal_code.txt)
            {"q": "§ 1950.5 Security Deposits and Tenant Protections", "file": "municipal_code.txt", "page": 1, "header": r"1950\.5|Security Deposits", "snippet": "one month's rent", "is_ocr": False},
            {"q": "one month rent unfurnished residential security deposit", "file": "municipal_code.txt", "page": 1, "header": r"1950\.5|Security Deposits", "snippet": "one month's rent", "is_ocr": False},
            {"q": "Section 8.22.030 Rent Adjustment Program Notice", "file": "municipal_code.txt", "page": 1, "header": r"8\.22\.030|Rent Adjustment", "snippet": "commencement of tenancy", "is_ocr": False},
            {"q": "failure to serve notice tolls statute of limitations rent disputes", "file": "municipal_code.txt", "page": 1, "header": r"8\.22\.030|Rent Adjustment", "snippet": "statute of limitations", "is_ocr": False},
            {"q": "Section 8.22.360 Just Cause for Eviction Ordinance", "file": "municipal_code.txt", "page": 1, "header": r"8\.22\.360|Just Cause", "snippet": "Just Cause grounds", "is_ocr": False},
            {"q": "recover possession rental unit enumerated grounds", "file": "municipal_code.txt", "page": 1, "header": r"8\.22\.360|Just Cause", "snippet": "recover possession", "is_ocr": False},
            {"q": "§ 1950.5", "file": "municipal_code.txt", "page": 1, "header": r"1950\.5", "snippet": "1950.5", "is_ocr": False},
            {"q": "Section 8.22.030", "file": "municipal_code.txt", "page": 1, "header": r"8\.22\.030", "snippet": "8.22.030", "is_ocr": False},
            {"q": "Section 8.22.360", "file": "municipal_code.txt", "page": 1, "header": r"8\.22\.360", "snippet": "8.22.360", "is_ocr": False},
            {"q": "Oakland Municipal Code rent adjustment notice", "file": "municipal_code.txt", "page": 1, "header": r"8\.22\.030|1950\.5", "snippet": "Rent Adjustment", "is_ocr": False},

            # 6. Tabular Vendor Spend Queries (vendor_matrix.csv)
            {"q": "Apex Cloud Infrastructure SLA Response Hours Annual Spend", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|Headers|General", "snippet": "Apex Cloud", "is_ocr": False},
            {"q": "Lexicon Legal Counsel SLA 4 hours Annual Spend 220000", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|Headers|General", "snippet": "Lexicon Legal", "is_ocr": False},
            {"q": "Acme Logistics Shipping Annual Spend 120000 ops@acme.com", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|Headers|General", "snippet": "Acme Logistics", "is_ocr": False},
            {"q": "ByteSafe Systems Security SLA 2 hours soc@bytesafe.org", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|Headers|General", "snippet": "ByteSafe Systems", "is_ocr": False},
            {"q": "vendor spend matrix annual spend 450000", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|Headers|General", "snippet": "450000", "is_ocr": False},
            {"q": "billing@lexicon.law counsel SLA response", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|Headers|General", "snippet": "billing@lexicon.law", "is_ocr": False},
            {"q": "support@apexcloud.io infrastructure SLA", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|Headers|General", "snippet": "support@apexcloud.io", "is_ocr": False},
            {"q": "Acme Logistics SLA response 24 hours", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|Headers|General", "snippet": "Acme Logistics", "is_ocr": False},
            {"q": "vendor annual spend 95000 security", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|Headers|General", "snippet": "95000", "is_ocr": False},
            {"q": "vendor category spend SLA contact table", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|Headers|General", "snippet": "Vendor Name", "is_ocr": False},
        ]

        recall_hits = 0
        citation_exact_matches = 0
        reciprocal_ranks: List[float] = []

        for idx, item in enumerate(benchmark_queries):
            query = item["q"]
            target_file = item["file"]
            target_page = item["page"]
            target_snippet = item["snippet"]

            hits = self.nexus.search(query, workspace="RegressionWorkspace", limit=5)
            self.assertGreater(len(hits), 0, f"Query '{query}' returned zero hits.")

            found_rank: Optional[int] = None
            for rank_idx, hit in enumerate(hits, start=1):
                if hit.filename == target_file:
                    found_rank = rank_idx
                    break

            if found_rank is not None:
                recall_hits += 1
                reciprocal_ranks.append(1.0 / found_rank)
            else:
                reciprocal_ranks.append(0.0)

            top_hit = hits[0]
            # Citation accuracy:
            # - If target has page number (e.g. PDF), must match physical page exactly.
            # - If target has page=None (DOCX, EML, CSV), page_number must be None and locator must be populated.
            citation_match = False
            if top_hit.filename == target_file:
                if target_page is not None:
                    if top_hit.page_number == target_page:
                        citation_match = True
                else:
                    if top_hit.page_number is None and (top_hit.locator or top_hit.header):
                        citation_match = True

            if citation_match:
                citation_exact_matches += 1

        total_queries = len(benchmark_queries)
        recall_at_5 = recall_hits / total_queries
        citation_accuracy = citation_exact_matches / total_queries
        mrr = sum(reciprocal_ranks) / total_queries

        print("\n=== EVAL_REGRESSION BENCHMARK RESULTS ===")
        print(f"Total Frozen Queries:      {total_queries}")
        print(f"Recall@5:                  {recall_at_5:.1%} ({recall_hits}/{total_queries})")
        print(f"Citation Exact Accuracy:   {citation_accuracy:.1%} ({citation_exact_matches}/{total_queries})")
        print(f"MRR:                       {mrr:.3f}")
        print("=========================================\n")

        # Invariant regression check: recall on frozen fixtures must remain 100%
        self.assertEqual(recall_at_5, 1.0, f"eval_regression broken: Recall@5 dropped to {recall_at_5:.1%}")
        self.assertGreaterEqual(citation_accuracy, 0.80, f"Citation accuracy dropped below threshold: {citation_accuracy:.1%}")
        self.assertGreaterEqual(mrr, 0.85, f"MRR dropped below threshold: {mrr:.3f}")


if __name__ == "__main__":
    unittest.main()
