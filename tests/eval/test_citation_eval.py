"""
KruschNexus Frozen Citation & Evaluation Harness (tests/eval/test_citation_eval.py)
=================================================================================
Hardened release gate measuring:
1. Recall@5 across a frozen 60-query multi-format benchmark
2. Exact 1-based Page Accuracy (PDF) & Structural Locator Accuracy (DOCX/CSV/EML)
3. Section Header Regex Accuracy
4. Ground-Truth Snippet Containment (retrieved text contains answer substring)
5. Scanned PDF High-Res OCR Precision & Recall
6. Negative & Distractor Query Resistance (RRF/section boost cannot cheat)
7. Strict 0.00% Cross-Workspace Privilege Isolation
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

        # Calculate p50 and p95 ingest latency
        durations = [r.duration_ms for r in cls.ingest_reports.values() if r.duration_ms]
        durations.sort()
        n = len(durations)
        cls.p50_ingest_ms = durations[n // 2] if n else 0.0
        cls.p95_ingest_ms = durations[min(n - 1, int(0.95 * n))] if n else 0.0

        # Calculate OCR page error rate
        expected_ocr_pages = 1
        scanned_rep = cls.ingest_reports.get("scanned_page.pdf")
        actual_ocr_pages = len(scanned_rep.ocr_pages) if scanned_rep else 0
        cls.ocr_page_error_rate = abs(expected_ocr_pages - actual_ocr_pages) / expected_ocr_pages

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
        self.assertIsNotNone(scanned_report.ocr_mean_confidence, "Mean OCR confidence must be recorded")
        self.assertGreaterEqual(scanned_report.ocr_mean_confidence, 0.70)

    def test_frozen_evaluation_suite(self):
        """
        Execute 60 frozen benchmark queries across the multi-format corpus.
        Calculates: Recall@5, Exact Page Accuracy, Header Accuracy, Snippet Containment, and OCR Precision.
        """
        benchmark_queries = [
            # ── 1. Vector PDF Contract Queries (sample_contract.pdf) ───────────
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

            # ── 2. Scanned PDF (High-Res OCR) Queries (scanned_page.pdf) ───────
            {"q": "liquidated damages", "file": "scanned_page.pdf", "page": 1, "header": r"14\.1|Liquidated", "snippet": "liquidated damages", "is_ocr": True},
            {"q": "Section 14.1 Liquidated Damages fifty thousand", "file": "scanned_page.pdf", "page": 1, "header": r"14\.1|Liquidated", "snippet": "fifty thousand", "is_ocr": True},
            {"q": "settlement release fifty thousand dollars", "file": "scanned_page.pdf", "page": 1, "header": r"14\.1|Liquidated|Settlement", "snippet": "fifty thousand", "is_ocr": True},
            {"q": "EXHIBIT B SCANNED SETTLEMENT RELEASE", "file": "scanned_page.pdf", "page": 1, "header": r"14\.1|Liquidated|Exhibit", "snippet": "EXHIBIT B", "is_ocr": True},
            {"q": "Executed this twenty-second day of September", "file": "scanned_page.pdf", "page": 1, "header": r"14\.1|Liquidated", "snippet": "twenty-second day", "is_ocr": True},
            {"q": "Section 14.1", "file": "scanned_page.pdf", "page": 1, "header": r"14\.1", "snippet": "Section 14.1", "is_ocr": True},
            {"q": "parties agree that liquidated damages shall be exactly", "file": "scanned_page.pdf", "page": 1, "header": r"14\.1", "snippet": "liquidated damages", "is_ocr": True},
            {"q": "settlement agreement release executed 2026", "file": "scanned_page.pdf", "page": 1, "header": r"14\.1", "snippet": "September 2026", "is_ocr": True},

            # ── 3. DOCX Nested Headings & Tables (policy_manual.docx) ──────────
            {"q": "backup retention standards", "file": "policy_manual.docx", "page": None, "header": r"1\.2|Backup Retention", "snippet": "Backup Retention Standards", "is_ocr": False},
            {"q": "Fleet Information Security Policy", "file": "policy_manual.docx", "page": None, "header": r"Section 1|Information Security", "snippet": "Information Security Policy", "is_ocr": False},
            {"q": "Privileged Corporate Paper Seven Years", "file": "policy_manual.docx", "page": None, "header": r"Backup Retention", "snippet": "Privileged Corporate Paper", "is_ocr": False},
            {"q": "Purging of records prior to expiration", "file": "policy_manual.docx", "page": None, "header": r"Backup Retention", "snippet": "Purging of records", "is_ocr": False},
            {"q": "policy violation compliance seven years", "file": "policy_manual.docx", "page": None, "header": r"Backup Retention", "snippet": "policy violation", "is_ocr": False},
            {"q": "All homelab nodes must operate completely air-gapped", "file": "policy_manual.docx", "page": None, "header": r"Section 1", "snippet": "air-gapped", "is_ocr": False},
            {"q": "unauthenticated ingress policy prohibition", "file": "policy_manual.docx", "page": None, "header": r"Section 1", "snippet": "unauthenticated ingress", "is_ocr": False},
            {"q": "Document archives in .ingested/ must be retained", "file": "policy_manual.docx", "page": None, "header": r"1\.2", "snippet": "ingested", "is_ocr": False},
            {"q": "Document Class Minimum Retention Period table", "file": "policy_manual.docx", "page": None, "header": r"1\.2", "snippet": "Minimum Retention Period", "is_ocr": False},
            {"q": "Seven (7) Years corporate records schedule", "file": "policy_manual.docx", "page": None, "header": r"1\.2", "snippet": "Seven (7) Years", "is_ocr": False},

            # ── 4. EML RFC2047 MIME & Attachments (deal_memo.eml) ──────────────
            {"q": "acquisition review protocol", "file": "deal_memo.eml", "page": None, "header": r"Acquisition|Review", "snippet": "acquisition review protocol", "is_ocr": False},
            {"q": "Section 4.5 purchase agreement closing", "file": "deal_memo.eml", "page": None, "header": r"Acquisition|deal_memo", "snippet": "Section 4.5 of the Purchase Agreement", "is_ocr": False},
            {"q": "General Counsel krusch.dev", "file": "deal_memo.eml", "page": None, "header": r"Acquisition|deal_memo", "snippet": "General Counsel", "is_ocr": False},
            {"q": "closing certificate schedule filing deadline", "file": "deal_memo.eml", "page": None, "header": r"Acquisition|Attachment", "snippet": "closing certificate", "is_ocr": False},
            {"q": "regulatory clearance purchase agreement", "file": "deal_memo.eml", "page": None, "header": r"Acquisition", "snippet": "regulatory clearance", "is_ocr": False},
            {"q": "Dear Executive Team review attached", "file": "deal_memo.eml", "page": None, "header": r"Acquisition", "snippet": "Executive Team", "is_ocr": False},
            {"q": "Section 4.5", "file": "deal_memo.eml", "page": None, "header": r"Acquisition|deal_memo", "snippet": "Section 4.5", "is_ocr": False},
            {"q": "Privileged - Acquisition Review Protocol", "file": "deal_memo.eml", "page": None, "header": r"Acquisition", "snippet": "Acquisition Review Protocol", "is_ocr": False},
            {"q": "filing deadline executive memo tomorrow", "file": "deal_memo.eml", "page": None, "header": r"Acquisition", "snippet": "filing deadline", "is_ocr": False},
            {"q": "general.counsel@krusch.dev", "file": "deal_memo.eml", "page": None, "header": r"Acquisition", "snippet": "general.counsel@krusch.dev", "is_ocr": False},

            # ── 5. Plain Text Statutory Codes (municipal_code.txt) ─────────────
            {"q": "§ 1950.5", "file": "municipal_code.txt", "page": None, "header": r"1950\.5|Security Deposits", "snippet": "1950.5", "is_ocr": False},
            {"q": "security deposits tenant protections unfurnished", "file": "municipal_code.txt", "page": None, "header": r"1950\.5", "snippet": "unfurnished residential property", "is_ocr": False},
            {"q": "one month rent unfurnished residential limit", "file": "municipal_code.txt", "page": None, "header": r"1950\.5", "snippet": "one month's rent", "is_ocr": False},
            {"q": "Section 8.22.030 Rent Adjustment Program", "file": "municipal_code.txt", "page": None, "header": r"8\.22\.030|Rent Adjustment", "snippet": "Rent Adjustment Program Notice", "is_ocr": False},
            {"q": "statute of limitations rent disputes written notice", "file": "municipal_code.txt", "page": None, "header": r"8\.22\.030", "snippet": "tolls the statute of limitations", "is_ocr": False},
            {"q": "commencement of tenancy notice requirement", "file": "municipal_code.txt", "page": None, "header": r"8\.22\.030", "snippet": "commencement of tenancy", "is_ocr": False},
            {"q": "Section 8.22.360 Just Cause for Eviction", "file": "municipal_code.txt", "page": None, "header": r"8\.22\.360|Just Cause", "snippet": "Just Cause for Eviction Ordinance", "is_ocr": False},
            {"q": "enumerated Just Cause grounds rental unit recovery", "file": "municipal_code.txt", "page": None, "header": r"8\.22\.360", "snippet": "enumerated Just Cause", "is_ocr": False},
            {"q": "OAKLAND MUNICIPAL CODE tenant protections", "file": "municipal_code.txt", "page": None, "header": r"1950|8\.22|Oakland", "snippet": "MUNICIPAL CODE", "is_ocr": False},
            {"q": "Section 8.22.030", "file": "municipal_code.txt", "page": None, "header": r"8\.22\.030", "snippet": "Section 8.22.030", "is_ocr": False},
            {"q": "Section 8.22.360", "file": "municipal_code.txt", "page": None, "header": r"8\.22\.360", "snippet": "Section 8.22.360", "is_ocr": False},
            {"q": "landlord demand or receive security deposit", "file": "municipal_code.txt", "page": None, "header": r"1950\.5", "snippet": "demand or receive security", "is_ocr": False},

            # ── 6. CSV Vendor Spend Matrix (vendor_matrix.csv) ─────────────────
            {"q": "Apex Cloud infrastructure spend", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|vendor_matrix", "snippet": "Apex Cloud", "is_ocr": False},
            {"q": "Lexicon Legal billing counsel SLA", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|vendor_matrix", "snippet": "Lexicon Legal", "is_ocr": False},
            {"q": "Acme Logistics shipping operations", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|vendor_matrix", "snippet": "Acme Logistics", "is_ocr": False},
            {"q": "ByteSafe Systems soc security spend", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|vendor_matrix", "snippet": "ByteSafe Systems", "is_ocr": False},
            {"q": "450000 annual spend cloud infrastructure", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|vendor_matrix", "snippet": "450000", "is_ocr": False},
            {"q": "support@apexcloud.io 1 hour SLA", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|vendor_matrix", "snippet": "support@apexcloud.io", "is_ocr": False},
            {"q": "billing@lexicon.law 220000 spend", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|vendor_matrix", "snippet": "billing@lexicon.law", "is_ocr": False},
            {"q": "soc@bytesafe.org 95000 annual", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|vendor_matrix", "snippet": "soc@bytesafe.org", "is_ocr": False},
            {"q": "ops@acme.com shipping 24 hours SLA", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|vendor_matrix", "snippet": "ops@acme.com", "is_ocr": False},
            {"q": "vendor contract matrix annual spend", "file": "vendor_matrix.csv", "page": None, "header": r"Rows|vendor_matrix", "snippet": "Vendor Name", "is_ocr": False},
        ]

        total = len(benchmark_queries)
        recall_5_count = 0
        mrr_sum = 0.0
        citation_exact_count = 0
        page_acc_count = 0
        header_acc_count = 0
        snippet_acc_count = 0
        ocr_total = sum(1 for b in benchmark_queries if b["is_ocr"])
        ocr_hit_count = 0

        print("\n" + "=" * 80)
        print(f"KRUSCHNEXUS FROZEN CITATION BENCHMARK ({total} MULTI-FORMAT QUERIES)")
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

            # MRR computation
            rr = 0.0
            for rank_idx, h in enumerate(hits, 1):
                if h.filename == b["file"]:
                    rr = 1.0 / rank_idx
                    break
            mrr_sum += rr

            if hits:
                top = hits[0]
                if top.filename == b["file"]:
                    # Exact page / locator check
                    page_matches = (b["page"] is None) or (top.page_number == b["page"])
                    if page_matches:
                        page_acc_count += 1

                    # Section header accuracy
                    header_str = (top.header or "") + " " + (top.locator or "") + " " + top.citation
                    header_matches = bool(re.search(b["header"], header_str, re.IGNORECASE))
                    if header_matches:
                        header_acc_count += 1

                    # Ground-truth snippet containment
                    snippet_matches = b["snippet"].lower() in top.text.lower()
                    if snippet_matches:
                        snippet_acc_count += 1

                    if page_matches and header_matches and snippet_matches:
                        citation_exact_count += 1

            status_str = "PASS" if target_in_top5 else "FAIL"
            top_cit = hits[0].citation if hits else "None"
            print(f"[{status_str}] Query: '{q[:42]:<42}' -> {top_cit}")

        recall_5 = recall_5_count / total
        mrr = mrr_sum / total
        citation_exact_match = citation_exact_count / total
        page_acc = page_acc_count / total
        header_acc = header_acc_count / total
        snippet_acc = snippet_acc_count / total
        ocr_recall = ocr_hit_count / max(1, ocr_total)

        print("-" * 80)
        print(f"Recall@5:             {recall_5 * 100:.1f}% ({recall_5_count}/{total})")
        print(f"MRR:                  {mrr:.3f}")
        print(f"Citation Exact-Match: {citation_exact_match * 100:.1f}% ({citation_exact_count}/{total})")
        print(f"OCR Page Error Rate:  {self.ocr_page_error_rate * 100:.1f}%")
        print(f"p50 Ingest Latency:   {self.p50_ingest_ms:.1f} ms")
        print(f"p95 Ingest Latency:   {self.p95_ingest_ms:.1f} ms")
        print(f"Page/Loc Accuracy:    {page_acc * 100:.1f}% ({page_acc_count}/{total})")
        print(f"Header Accuracy:      {header_acc * 100:.1f}% ({header_acc_count}/{total})")
        print(f"Snippet Containment:  {snippet_acc * 100:.1f}% ({snippet_acc_count}/{total})")
        print(f"OCR Recall:           {ocr_recall * 100:.1f}% ({ocr_hit_count}/{ocr_total})")
        print("=" * 80)

        # Release Gating Contracts
        self.assertGreaterEqual(recall_5, 0.92, f"Recall@5 ({recall_5:.2%}) must be >= 92%")
        self.assertGreaterEqual(mrr, 0.85, f"MRR ({mrr:.3f}) must be >= 0.85")
        self.assertGreaterEqual(citation_exact_match, 0.80, f"Citation exact-match ({citation_exact_match:.2%}) must be >= 80%")
        self.assertLessEqual(self.ocr_page_error_rate, 0.05, f"OCR page error rate ({self.ocr_page_error_rate:.2%}) must be <= 5%")
        self.assertEqual(ocr_recall, 1.0, f"OCR Recall must be 100%")

    def test_negative_and_distractor_query_resistance(self):
        """
        Verify that negative, out-of-corpus, and near-miss distractor queries
        do not trick RRF or statutory section boost into returning false positive hits.
        """
        distractor_queries = [
            # Statute NOT in corpus: § 1954 (Landlord right of entry)
            {"q": "§ 1954 Landlord right to enter dwelling unit twenty-four hours notice", "unwanted_sec": "1954"},
            # Phantom section number not present in any contract
            {"q": "Section 99.9 Environmental Indemnity Hazardous Waste", "unwanted_sec": "99.9"},
            # Near-miss statute: § 1942.5 (Retaliation) vs § 1950.5 (Deposits)
            {"q": "§ 1942.5 Retaliatory eviction defense", "unwanted_sec": "1942.5"},
        ]

        for dq in distractor_queries:
            hits = self.nexus.search(
                query=dq["q"],
                workspace="BenchmarkWorkspace",
                limit=5
            )
            for h in hits:
                full_h = (h.header or "") + " " + (h.locator or "")
                self.assertNotIn(
                    dq["unwanted_sec"],
                    full_h,
                    f"Distractor token '{dq['unwanted_sec']}' falsely captured in header '{full_h}'!"
                )

    def test_workspace_isolation_zero_leakage(self):
        """
        Assert that searching Workspace A never returns documents from Workspace B.
        Workspace leakage rate must strictly equal 0.00%.
        """
        isolated_queries = [
            "liquidated damages fifty thousand dollars",
            "fleet information security air-gapped",
            "acquisition review protocol closing certificate",
            "Section 8.22 Permitted Use commercial lease",
            "Apex Cloud infrastructure spend 450000"
        ]

        leaked_count = 0
        for q in isolated_queries:
            hits = self.nexus.search(
                query=q,
                workspace="IsolatedWorkspace_Beta",
                limit=5
            )
            for h in hits:
                if h.filename != "municipal_code.txt":
                    leaked_count += 1

        leak_rate = leaked_count / len(isolated_queries)
        print(f"\n[ISOLATION TEST] Cross-Workspace Leak Rate: {leak_rate * 100:.2f}% (Leaked: {leaked_count})")
        self.assertEqual(leak_rate, 0.0, "Workspace isolation failed: Cross-workspace document leakage detected!")


if __name__ == "__main__":
    unittest.main()
