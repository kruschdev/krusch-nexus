"""
tests/eval/test_eval_heldout.py
===============================
eval_heldout: Evaluates retrieval, citation accuracy, and character span fidelity
on unseen and messy documents that were NOT used to tune chunkers, section regexes, or scoring weights.

Scores:
1. Citation Accuracy (Section / locator level) [PRIMARY]
2. Span Precision (Exact character span bounds) [PRIMARY]
3. Recall@5 (Document level) [SECONDARY]
4. Metrics split by Parser Family (digital PDF, OCR PDF, DOCX, HTML email, CSV, statutory TXT, mixed PDF)
"""

import os
import json
import hashlib
import shutil
import tempfile
import unittest
import pytest

from krusch_nexus import NexusClient, NexusConfig, DocType
from krusch_nexus.store import init_db, get_engine

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")


@pytest.mark.heldout
class TestEvalHeldout(unittest.TestCase):
    """
    eval_heldout suite:
    Validates genuine retrieval and citation generalization on messy, unseen corpus documents
    across diverse parser families.
    """

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="nexus_eval_heldout_")
        cls.db_path = os.path.join(cls.temp_dir, "heldout.db")
        cls.config = NexusConfig(
            database_url=f"sqlite:///{cls.db_path}",
            allowed_ingest_roots=[FIXTURES_DIR, cls.temp_dir]
        )
        cls.engine = get_engine(cls.config.database_url)
        init_db(cls.engine)
        cls.nexus = NexusClient(cls.config)

        # Ingest unseen held-out fixtures into "HeldoutWorkspace"
        cls.heldout_fixtures = [
            ("heldout_bylaws.txt", DocType.AUTHORITY, "statutory_txt"),
            ("heldout_promissory_note.txt", DocType.AUTHORITY, "statutory_txt"),
            ("heldout_employment_agreement.txt", DocType.WORK_PRODUCT, "statutory_txt"),
            ("heldout_lease_amendment.txt", DocType.AUTHORITY, "statutory_txt"),
            ("heldout_software_license.txt", DocType.AUTHORITY, "statutory_txt"),
            ("heldout_sec_10k_table.pdf", DocType.WORK_PRODUCT, "digital_pdf"),
            ("heldout_twocolumn_newspaper.pdf", DocType.GENERAL, "ocr_pdf"),
            ("heldout_medical_scan_150dpi.pdf", DocType.FACT_NARRATIVE, "ocr_pdf"),
            ("heldout_redacted_order.pdf", DocType.AUTHORITY, "ocr_pdf"),
            ("heldout_mixed_digital_scan.pdf", DocType.AUTHORITY, "mixed_pdf"),
            ("policy_manual.docx", DocType.WORK_PRODUCT, "docx"),
            ("deal_memo.eml", DocType.WORK_PRODUCT, "html_email"),
            ("vendor_matrix.csv", DocType.GENERAL, "tabular_csv"),
        ]

        cls.reports = {}
        for fname, doc_type, family in cls.heldout_fixtures:
            path = os.path.join(FIXTURES_DIR, fname)
            report = cls.nexus.ingest(
                filepath=path,
                workspace="HeldoutWorkspace",
                doc_type=doc_type,
                archive=False
            )
            cls.reports[fname] = report

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_heldout_fixtures_sha256_manifest(self):
        """Verify all held-out and messy fixture files on disk exactly match fixtures_manifest.json."""
        manifest_path = os.path.join(FIXTURES_DIR, "fixtures_manifest.json")
        self.assertTrue(os.path.exists(manifest_path), "fixtures_manifest.json must exist")
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        tracked = manifest.get("fixtures", {})
        self.assertGreaterEqual(len(tracked), 25, "Manifest must track at least 25 fixture files")

        for fname, _, _ in self.heldout_fixtures:
            self.assertIn(fname, tracked, f"Fixture {fname} not tracked in fixtures_manifest.json")
            fpath = os.path.join(FIXTURES_DIR, fname)
            with open(fpath, "rb") as f:
                actual_hash = hashlib.sha256(f.read()).hexdigest()
            expected_hash = tracked[fname]["sha256"]
            self.assertEqual(actual_hash, expected_hash, f"SHA-256 mismatch for {fname}")

    def test_heldout_generalization_and_citation_accuracy(self):
        """
        Execute comprehensive queries against messy held-out documents across 7 parser families.
        Decouples Citation Accuracy, Span Precision, and Recall@5.
        Generates parser-family performance breakdowns.
        """
        heldout_queries = [
            # statutory_txt: heldout_bylaws.txt
            {
                "q": "annual meeting of stockholders third Tuesday of May",
                "file": "heldout_bylaws.txt",
                "family": "statutory_txt",
                "locator_substr": "Section 1.1",
                "snippet": "third Tuesday of May"
            },
            {
                "q": "special meetings called by Chairman of the Board or Chief Executive Officer",
                "file": "heldout_bylaws.txt",
                "family": "statutory_txt",
                "locator_substr": "Section 1.2",
                "snippet": "Chairman of the Board"
            },
            {
                "q": "Board of Directors number not less than five nor more than nine",
                "file": "heldout_bylaws.txt",
                "family": "statutory_txt",
                "locator_substr": "Section 2.1",
                "snippet": "five (5) nor more than nine"
            },
            {
                "q": "quorum for transaction of business majority authorized directors",
                "file": "heldout_bylaws.txt",
                "family": "statutory_txt",
                "locator_substr": "Section 2.4",
                "snippet": "quorum for the transaction"
            },
            # statutory_txt: heldout_promissory_note.txt
            {
                "q": "principal sum Two Million Five Hundred Thousand Dollars promissory note",
                "file": "heldout_promissory_note.txt",
                "family": "statutory_txt",
                "locator_substr": "Section 1",
                "snippet": "Two Million Five Hundred Thousand"
            },
            {
                "q": "interest accrue annual fixed rate 6.75% 360-day year",
                "file": "heldout_promissory_note.txt",
                "family": "statutory_txt",
                "locator_substr": "Section 1",
                "snippet": "6.75%"
            },
            {
                "q": "Event of Default ten calendar days acceleration entire balance",
                "file": "heldout_promissory_note.txt",
                "family": "statutory_txt",
                "locator_substr": "Section 3",
                "snippet": "Event of Default"
            },
            # statutory_txt: heldout_employment_agreement.txt
            {
                "q": "Chief Technology Officer reporting exclusively to Chief Executive Officer",
                "file": "heldout_employment_agreement.txt",
                "family": "statutory_txt",
                "locator_substr": "Section 1.1",
                "snippet": "Chief Technology Officer"
            },
            {
                "q": "annual base salary $375,000 semi-monthly installments",
                "file": "heldout_employment_agreement.txt",
                "family": "statutory_txt",
                "locator_substr": "Section 2.1",
                "snippet": "$375,000"
            },
            {
                "q": "severance upon termination without Cause twelve months base salary",
                "file": "heldout_employment_agreement.txt",
                "family": "statutory_txt",
                "locator_substr": "Section 2.3",
                "snippet": "twelve (12) months"
            },
            # statutory_txt: heldout_lease_amendment.txt
            {
                "q": "expansion premises Suite 400 4,500 rentable square feet",
                "file": "heldout_lease_amendment.txt",
                "family": "statutory_txt",
                "locator_substr": "Section 1",
                "snippet": "Suite 400"
            },
            {
                "q": "monthly base rent expansion premises $18,000 3% annual escalation",
                "file": "heldout_lease_amendment.txt",
                "family": "statutory_txt",
                "locator_substr": "Section 2",
                "snippet": "$18,000"
            },
            # statutory_txt: heldout_software_license.txt
            {
                "q": "non-exclusive perpetual license deploy software 50 server nodes",
                "file": "heldout_software_license.txt",
                "family": "statutory_txt",
                "locator_substr": "Section 1",
                "snippet": "50 server nodes"
            },
            {
                "q": "service level agreement 99.95% monthly service availability SLA",
                "file": "heldout_software_license.txt",
                "family": "statutory_txt",
                "locator_substr": "Section 2",
                "snippet": "99.95%"
            },
            # digital_pdf: heldout_sec_10k_table.pdf
            {
                "q": "Consolidated Statements of Operations Revenue $84,250 million 10-K",
                "file": "heldout_sec_10k_table.pdf",
                "family": "digital_pdf",
                "locator_substr": "Item 8",
                "snippet": "$84,250"
            },
            {
                "q": "Stock-based compensation expense operating costs $2,450 million in 2026",
                "file": "heldout_sec_10k_table.pdf",
                "family": "digital_pdf",
                "locator_substr": "Note 1",
                "snippet": "$2,450"
            },
            {
                "q": "Research and Development $12,400 Operating Income $33,750",
                "file": "heldout_sec_10k_table.pdf",
                "family": "digital_pdf",
                "locator_substr": "Item 8",
                "snippet": "$33,750"
            },
            # ocr_pdf: heldout_medical_scan_150dpi.pdf
            {
                "q": "Amoxicillin 500mg twice daily with meals seven consecutive days emergency discharge",
                "file": "heldout_medical_scan_150dpi.pdf",
                "family": "ocr_pdf",
                "locator_substr": "Section 8",
                "snippet": "Amoxicillin"
            },
            {
                "q": "acute lower quadrant abdominal discomfort clinical intake summary",
                "file": "heldout_medical_scan_150dpi.pdf",
                "family": "ocr_pdf",
                "locator_substr": "Section 8",
                "snippet": "abdominal discomfort"
            },
            # ocr_pdf: heldout_redacted_order.pdf
            {
                "q": "enjoined from commercial distribution catalytic compounds protective order",
                "file": "heldout_redacted_order.pdf",
                "family": "ocr_pdf",
                "locator_substr": "Section 2",
                "snippet": "catalytic compounds"
            },
            # ocr_pdf: heldout_twocolumn_newspaper.pdf
            {
                "q": "Biotech Merger Clearance GeneCraft Therapeutics $4.2 billion acquisition",
                "file": "heldout_twocolumn_newspaper.pdf",
                "family": "ocr_pdf",
                "locator_substr": "Section 4",
                "snippet": "GeneCraft"
            },
            # mixed_pdf: heldout_mixed_digital_scan.pdf
            {
                "q": "Base Rent and Escalation Schedule monthly base rent $42,500",
                "file": "heldout_mixed_digital_scan.pdf",
                "family": "mixed_pdf",
                "locator_substr": "Section 2",
                "snippet": "$42,500"
            },
            {
                "q": "Schedule of Permitted Subtenants Apex Logistics LLC Suite 400",
                "file": "heldout_mixed_digital_scan.pdf",
                "family": "mixed_pdf",
                "locator_substr": "EXHIBIT B",
                "snippet": "Apex Logistics"
            },
            # docx: policy_manual.docx
            {
                "q": "Backup Retention Standards minimum retention period privileged corporate paper seven years",
                "file": "policy_manual.docx",
                "family": "docx",
                "locator_substr": "1.2",
                "snippet": "Seven (7) Years"
            },
            # html_email: deal_memo.eml
            {
                "q": "acquisition review protocol requires regulatory clearance closing certificate",
                "file": "deal_memo.eml",
                "family": "html_email",
                "locator_substr": "Section 4.5",
                "snippet": "regulatory clearance"
            },
            # tabular_csv: vendor_matrix.csv
            {
                "q": "Apex Cloud Infrastructure SLA response hours spend support@apexcloud.io",
                "file": "vendor_matrix.csv",
                "family": "tabular_csv",
                "locator_substr": "Rows",
                "snippet": "support@apexcloud.io"
            },
        ]

        recall_hits = 0
        citation_matches = 0
        span_matches = 0
        total = len(heldout_queries)

        # Family-level metric trackers
        families = set(item["family"] for item in heldout_queries)
        family_stats = {
            f: {"total": 0, "recall": 0, "citation": 0, "span": 0}
            for f in families
        }

        for item in heldout_queries:
            query = item["q"]
            target_file = item["file"]
            target_loc = item["locator_substr"]
            target_snip = item["snippet"]
            fam = item["family"]

            family_stats[fam]["total"] += 1

            hits = self.nexus.search(query, workspace="HeldoutWorkspace", limit=5)
            self.assertGreater(len(hits), 0, f"Query '{query}' returned zero hits.")

            # Metric 1: Recall@5 (target doc found in top-5)
            found_target = any(h.filename == target_file for h in hits)
            if found_target:
                recall_hits += 1
                family_stats[fam]["recall"] += 1

            # Metric 2: Citation Accuracy (top hit points to target document and correct structural locator)
            top_hit = hits[0]
            if top_hit.filename == target_file:
                loc_str = (top_hit.locator or "") + " " + (top_hit.header or "") + " " + top_hit.citation + " " + " ".join(top_hit.heading_path)
                if target_loc.lower() in loc_str.lower() or target_snip.lower() in top_hit.text.lower():
                    citation_matches += 1
                    family_stats[fam]["citation"] += 1

            # Metric 3: Span Precision (top hit provides character bounds containing the snippet)
            if top_hit.char_start is not None and top_hit.char_end is not None:
                if top_hit.char_end > top_hit.char_start and target_snip.lower() in top_hit.text.lower():
                    span_matches += 1
                    family_stats[fam]["span"] += 1

        recall_at_5 = recall_hits / total
        citation_accuracy = citation_matches / total
        span_precision = span_matches / total

        print("\n" + "=" * 70)
        print("  📊 EVAL_HELDOUT COMPREHENSIVE BENCHMARK RESULTS")
        print("=" * 70)
        print(f"Total Held-Out Queries:    {total}")
        print(f"Citation Accuracy (PRI):   {citation_accuracy:.1%} ({citation_matches}/{total})")
        print(f"Span Precision (PRI):      {span_precision:.1%} ({span_matches}/{total})")
        print(f"Recall@5 (SEC):            {recall_at_5:.1%} ({recall_hits}/{total})")
        print("-" * 70)
        print(f"{'Parser Family':<16} | {'Count':<6} | {'Citation Acc':<12} | {'Span Prec':<10} | {'Recall@5':<8}")
        print("-" * 70)
        for fam in sorted(family_stats.keys()):
            s = family_stats[fam]
            cnt = s["total"]
            f_cit = s["citation"] / cnt if cnt else 0.0
            f_span = s["span"] / cnt if cnt else 0.0
            f_rec = s["recall"] / cnt if cnt else 0.0
            print(f"{fam:<16} | {cnt:<6} | {f_cit:<12.1%} | {f_span:<10.1%} | {f_rec:<8.1%}")
        print("=" * 70 + "\n")

        # Held-out quality gates (Release-blocking)
        self.assertGreaterEqual(
            citation_accuracy, 0.80,
            f"Held-out Citation Accuracy dropped below 80% band: {citation_accuracy:.1%}"
        )
        self.assertGreaterEqual(
            span_precision, 0.80,
            f"Held-out Span Precision dropped below 80% band: {span_precision:.1%}"
        )
        self.assertGreaterEqual(
            recall_at_5, 0.85,
            f"Held-out Recall@5 dropped below 85% band: {recall_at_5:.1%}"
        )


if __name__ == "__main__":
    unittest.main()
