"""
KruschNexus Citation & Page-True Evaluation Harness
===================================================
Evaluates the full closed-loop ingestion and hybrid retrieval pipeline on a fixed corpus:
- Commercial lease PDF (multi-page)
- Scanned settlement release PDF (requiring OCR)
- Fleet policy DOCX with structural headings
- Privileged deal memo EML
- Municipal code statutory text (§ and Section numbers)

Measures Recall@5 of the exact expected page and canonical citation.
"""

import os
import shutil
import tempfile
import unittest
from typing import List, Dict, Any

from src.backend.config import NexusConfig
from src.backend.db import init_db, get_engine
from src.backend.client import NexusIngestClient
from src.backend.models import SearchHit

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


class TestCitationEvaluation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="nexus_eval_")
        cls.db_path = os.path.join(cls.temp_dir, "eval.db")
        cls.config = NexusConfig(
            database_url=f"sqlite:///{cls.db_path}",
            ocr_threshold_chars=30,
            ocr_dpi=150
        )
        cls.engine = get_engine(cls.config.database_url)
        init_db(cls.engine)
        cls.client = NexusIngestClient(cls.config)

        # Ingest the entire fixed corpus into workspace "BenchmarkWorkspace"
        cls.fixtures = [
            ("sample_contract.pdf", "sample_contract.pdf", "authority"),
            ("scanned_page.pdf", "scanned_page.pdf", "authority"),
            ("policy_manual.docx", "policy_manual.docx", "work_product"),
            ("deal_memo.eml", "deal_memo.eml", "general"),
            ("municipal_code.txt", "municipal_code.txt", "authority"),
        ]

        cls.ingest_reports = {}
        for fname, disk_name, doc_type in cls.fixtures:
            path = os.path.join(FIXTURES_DIR, disk_name)
            report = cls.client.ingest_file(
                filepath=path,
                workspace="BenchmarkWorkspace",
                doc_type=doc_type,
                archive=False
            )
            cls.ingest_reports[fname] = report

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_corpus_ingestion_integrity(self):
        """Verify all 5 test corpus files were successfully ingested."""
        self.assertEqual(len(self.ingest_reports), 5)
        for fname, report in self.ingest_reports.items():
            self.assertEqual(report.status, "completed", f"Failed to ingest {fname}: {report.error}")
            self.assertGreater(report.total_chunks, 0)

        # Verify OCR was specifically applied to the scanned PDF fixture
        scanned_report = self.ingest_reports["scanned_page.pdf"]
        self.assertIn(1, scanned_report.ocr_pages, "Page 1 of scanned_page.pdf must have triggered OCR fallback")

    def test_recall_at_5_exact_page_citation(self):
        """
        Execute targeted queries and measure Recall@5 on exact document filename
        and 1-based page number.
        """
        eval_cases = [
            {
                "query": "Permitted use of premises Section 8.22",
                "expected_file": "sample_contract.pdf",
                "expected_page": 1,
                "expected_sec": "8.22"
            },
            {
                "query": "Termination for breach and thirty day cure Section 19.3",
                "expected_file": "sample_contract.pdf",
                "expected_page": 2,
                "expected_sec": "19.3"
            },
            {
                "query": "Liquidated damages fifty thousand dollars Section 14.1",
                "expected_file": "scanned_page.pdf",
                "expected_page": 1,
                "expected_sec": "14"
            },
            {
                "query": "Backup retention standards Section 1.2 seven years",
                "expected_file": "policy_manual.docx",
                "expected_page": 1,
                "expected_sec": "1.2"
            },
            {
                "query": "Purchase Agreement closing conditions regulatory clearance Section 4.5",
                "expected_file": "deal_memo.eml",
                "expected_page": 1,
                "expected_sec": "4.5"
            },
            {
                "query": "Security deposit limit under § 1950.5",
                "expected_file": "municipal_code.txt",
                "expected_page": 1,
                "expected_sec": "1950.5"
            },
            {
                "query": "Rent adjustment program written notice Section 8.22.030",
                "expected_file": "municipal_code.txt",
                "expected_page": 1,
                "expected_sec": "8.22.030"
            },
        ]

        total_cases = len(eval_cases)
        correct_hits = 0

        print("\n" + "=" * 70)
        print("KRUSCHNEXUS EVALUATION: RECALL@5 ON PAGE-TRUE CITATIONS")
        print("=" * 70)

        for case in eval_cases:
            query = case["query"]
            hits: List[SearchHit] = self.client.search(
                query=query,
                workspace="BenchmarkWorkspace",
                limit=5
            )

            # Check if expected document and page is in top-5 hits
            match_found = False
            top_cit = hits[0].citation if hits else "[No hits]"
            for rank, hit in enumerate(hits, start=1):
                if hit.filename == case["expected_file"] and hit.page_number == case["expected_page"]:
                    match_found = True
                    break

            status_mark = "PASS" if match_found else "FAIL"
            print(f"[{status_mark}] Query: '{query}'")
            print(f"       Expected: [{case['expected_file']}, p. {case['expected_page']}]")
            print(f"       Top Hit:  {top_cit}")

            if match_found:
                correct_hits += 1

        recall_at_5 = correct_hits / total_cases
        print("-" * 70)
        print(f"Recall@5: {recall_at_5 * 100:.1f}% ({correct_hits}/{total_cases} queries retrieved correct page)")
        print("=" * 70)

        self.assertEqual(recall_at_5, 1.0, f"Recall@5 fell below 100% ({correct_hits}/{total_cases})")


if __name__ == "__main__":
    unittest.main()
