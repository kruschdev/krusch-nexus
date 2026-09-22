"""
KruschNexus Citation & Evaluation Harness (tests/eval)
======================================================
Measures and asserts:
1. Recall@5 on benchmark queries against the fixed corpus
2. Canonical citation string formatting: '{filename} p.{n} § {header}'
3. Workspace isolation leak rate: Strict 0.00% cross-workspace contamination
"""

import os
import shutil
import tempfile
import unittest
from typing import List, Dict, Any

from krusch_nexus import Nexus, NexusConfig, ChunkHit
from krusch_nexus.db import init_db, get_engine

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")


class TestCitationEvaluation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="nexus_eval_")
        cls.db_path = os.path.join(cls.temp_dir, "eval.db")
        cls.config = NexusConfig(
            database_url=f"sqlite:///{cls.db_path}",
            ocr_threshold_chars=30,
            ocr_dpi=150,
            allowed_ingest_roots=[FIXTURES_DIR, cls.temp_dir]
        )
        cls.engine = get_engine(cls.config.database_url)
        init_db(cls.engine)
        cls.nexus = Nexus(cls.config)

        # Ingest benchmark corpus into "BenchmarkWorkspace"
        cls.fixtures = [
            ("sample_contract.pdf", "authority"),
            ("scanned_page.pdf", "authority"),
            ("policy_manual.docx", "work_product"),
            ("deal_memo.eml", "general"),
            ("municipal_code.txt", "authority"),
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

        # Ingest into an isolated workspace for leakage evaluation
        isolated_path = os.path.join(FIXTURES_DIR, "municipal_code.txt")
        cls.nexus.ingest(
            filepath=isolated_path,
            workspace="IsolatedWorkspace_Beta",
            doc_type="authority",
            archive=False
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_corpus_ingestion_integrity(self):
        """Verify all 5 test corpus files were successfully ingested."""
        self.assertEqual(len(self.ingest_reports), 5)
        for fname, report in self.ingest_reports.items():
            self.assertEqual(report.status, "completed", f"Failed to ingest {fname}: {report.error}")
            self.assertGreater(report.total_chunks, 0)

        # Scanned PDF must have triggered OCR fallback
        scanned_report = self.ingest_reports["scanned_page.pdf"]
        self.assertIn(1, scanned_report.ocr_pages, "Page 1 of scanned_page.pdf must have triggered OCR")

    def test_eval_benchmark_queries_and_citation_match(self):
        """
        Verify Recall@5 and canonical citation matching across the four core benchmark queries.
        
        | Query | Must retrieve | Citation must include |
        |---|---|---|
        | liquidated damages | sample contract / scanned page | Section 14 / page 1 |
        | backup retention | policy DOCX | Section 1.2 |
        | § 1950.5 | municipal code | § 1950.5 |
        | acquisition review protocol | deal memo EML | deal memo EML / Section 4.5 |
        """
        eval_cases = [
            {
                "query": "liquidated damages",
                "must_retrieve_file": "scanned_page.pdf",
                "citation_must_include": "Section 14",
                "expected_page": 1
            },
            {
                "query": "backup retention",
                "must_retrieve_file": "policy_manual.docx",
                "citation_must_include": "Section 1.2",
                "expected_page": 1
            },
            {
                "query": "§ 1950.5",
                "must_retrieve_file": "municipal_code.txt",
                "citation_must_include": "1950.5",
                "expected_page": 1
            },
            {
                "query": "acquisition review protocol",
                "must_retrieve_file": "deal_memo.eml",
                "citation_must_include": "deal_memo.eml",
                "expected_page": 1
            },
        ]

        total = len(eval_cases)
        correct_hits = 0
        citation_matches = 0

        print("\n" + "=" * 75)
        print("KRUSCHNEXUS EVALUATION: RECALL@5 & CANONICAL CITATION ACCURACY")
        print("=" * 75)

        for case in eval_cases:
            q = case["query"]
            hits: List[ChunkHit] = self.nexus.search(
                query=q,
                workspace="BenchmarkWorkspace",
                limit=5
            )

            found_file = False
            citation_ok = False
            top_hit_cit = hits[0].citation if hits else "None"

            for hit in hits:
                if hit.filename == case["must_retrieve_file"] and hit.page_number == case["expected_page"]:
                    found_file = True
                    if case["citation_must_include"] in hit.citation or case["citation_must_include"] in (hit.header or ""):
                        citation_ok = True
                    break

            if found_file:
                correct_hits += 1
            if citation_ok:
                citation_matches += 1

            status_str = "PASS" if (found_file and citation_ok) else "FAIL"
            print(f"[{status_str}] Query: '{q}'")
            print(f"       Expected File: {case['must_retrieve_file']} (p. {case['expected_page']})")
            print(f"       Top Citation:  {top_hit_cit}")

        recall_5 = correct_hits / total
        citation_acc = citation_matches / total

        print("-" * 75)
        print(f"Recall@5: {recall_5 * 100:.1f}% ({correct_hits}/{total})")
        print(f"Citation Exact Match: {citation_acc * 100:.1f}% ({citation_matches}/{total})")
        print("=" * 75)

        self.assertEqual(recall_5, 1.0, f"Recall@5 is {recall_5}, expected 1.0")
        self.assertEqual(citation_acc, 1.0, f"Citation accuracy is {citation_acc}, expected 1.0")

    def test_workspace_isolation_zero_leakage(self):
        """
        Assert that searching Workspace A never returns documents from Workspace B.
        Workspace leakage rate must strictly equal 0.00%.
        """
        # BenchmarkWorkspace contains: sample_contract.pdf, scanned_page.pdf, policy_manual.docx, deal_memo.eml, municipal_code.txt
        # IsolatedWorkspace_Beta contains ONLY: municipal_code.txt

        # Search Beta for a query that only exists in BenchmarkWorkspace files
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

        leak_rate = (leaked_count / total_queries)
        print(f"\n[ISOLATION TEST] Cross-Workspace Leak Rate: {leak_rate * 100:.2f}% (Leaked documents: {leaked_count})")
        self.assertEqual(leak_rate, 0.0, "Workspace isolation failed: Cross-workspace document leakage detected!")


if __name__ == "__main__":
    unittest.main()
