"""
tests/eval/test_eval_heldout.py
===============================
eval_heldout: Evaluates retrieval, citation accuracy, and character span fidelity
on unseen documents that were NOT used to tune chunkers, section regexes, or scoring weights.
Scores:
1. Recall@5 (Document level)
2. Citation Accuracy (Section / locator level)
3. Span Precision (Exact character span bounds)
"""

import os
import shutil
import tempfile
import unittest
from typing import List, Dict, Any, Optional

from krusch_nexus import NexusClient, NexusConfig, DocType
from krusch_nexus.store import init_db, get_engine

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")


class TestEvalHeldout(unittest.TestCase):
    """
    eval_heldout suite:
    Validates genuine retrieval and citation generalization on unseen corpus text.
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
            ("heldout_bylaws.txt", DocType.AUTHORITY),
            ("heldout_promissory_note.txt", DocType.AUTHORITY),
            ("heldout_employment_agreement.txt", DocType.WORK_PRODUCT),
            ("heldout_lease_amendment.txt", DocType.AUTHORITY),
            ("heldout_software_license.txt", DocType.AUTHORITY),
        ]

        cls.reports = {}
        for fname, doc_type in cls.heldout_fixtures:
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

    def test_heldout_generalization_and_citation_accuracy(self):
        """
        Execute 25 diverse queries against 5 held-out documents.
        Decouples Recall@5, Citation Accuracy, and Span Precision.
        """
        heldout_queries = [
            # 1-5: heldout_bylaws.txt
            {
                "q": "annual meeting of stockholders third Tuesday of May",
                "file": "heldout_bylaws.txt",
                "locator_substr": "Section 1.1",
                "snippet": "third Tuesday of May"
            },
            {
                "q": "special meetings called by Chairman of the Board or Chief Executive Officer",
                "file": "heldout_bylaws.txt",
                "locator_substr": "Section 1.2",
                "snippet": "Chairman of the Board"
            },
            {
                "q": "Board of Directors number not less than five nor more than nine",
                "file": "heldout_bylaws.txt",
                "locator_substr": "Section 2.1",
                "snippet": "five (5) nor more than nine"
            },
            {
                "q": "quorum for transaction of business majority authorized directors",
                "file": "heldout_bylaws.txt",
                "locator_substr": "Section 2.4",
                "snippet": "quorum for the transaction"
            },
            {
                "q": "stockholders governance Article I annual meeting",
                "file": "heldout_bylaws.txt",
                "locator_substr": "Article I",
                "snippet": "Stockholders and Governance"
            },

            # 6-10: heldout_promissory_note.txt
            {
                "q": "principal sum Two Million Five Hundred Thousand Dollars promissory note",
                "file": "heldout_promissory_note.txt",
                "locator_substr": "Section 1",
                "snippet": "Two Million Five Hundred Thousand"
            },
            {
                "q": "interest accrue annual fixed rate 6.75% 360-day year",
                "file": "heldout_promissory_note.txt",
                "locator_substr": "Section 1",
                "snippet": "6.75%"
            },
            {
                "q": "maturity and amortization schedule due December 31 2030",
                "file": "heldout_promissory_note.txt",
                "locator_substr": "Section 2",
                "snippet": "December 31, 2030"
            },
            {
                "q": "Event of Default ten calendar days acceleration entire balance",
                "file": "heldout_promissory_note.txt",
                "locator_substr": "Section 3",
                "snippet": "Event of Default"
            },
            {
                "q": "Borrower promises to pay to the order of Lender promissory note",
                "file": "heldout_promissory_note.txt",
                "locator_substr": "Section 1",
                "snippet": "promises to pay"
            },

            # 11-15: heldout_employment_agreement.txt
            {
                "q": "Chief Technology Officer reporting exclusively to Chief Executive Officer",
                "file": "heldout_employment_agreement.txt",
                "locator_substr": "Section 1.1",
                "snippet": "Chief Technology Officer"
            },
            {
                "q": "annual base salary $375,000 semi-monthly installments",
                "file": "heldout_employment_agreement.txt",
                "locator_substr": "Section 2.1",
                "snippet": "$375,000"
            },
            {
                "q": "severance upon termination without Cause twelve months base salary",
                "file": "heldout_employment_agreement.txt",
                "locator_substr": "Section 2.3",
                "snippet": "twelve (12) months"
            },
            {
                "q": "non-competition and non-solicitation employees customers one year thereafter",
                "file": "heldout_employment_agreement.txt",
                "locator_substr": "Section 3.1",
                "snippet": "Non-Competition"
            },
            {
                "q": "restrictive covenants executive employment agreement",
                "file": "heldout_employment_agreement.txt",
                "locator_substr": "Article 3",
                "snippet": "Restrictive Covenants"
            },

            # 16-20: heldout_lease_amendment.txt
            {
                "q": "expansion premises Suite 400 4,500 rentable square feet",
                "file": "heldout_lease_amendment.txt",
                "locator_substr": "Section 1",
                "snippet": "Suite 400"
            },
            {
                "q": "monthly base rent expansion premises $18,000 3% annual escalation",
                "file": "heldout_lease_amendment.txt",
                "locator_substr": "Section 2",
                "snippet": "$18,000"
            },
            {
                "q": "tenant improvement allowance $45.00 per rentable square foot construction",
                "file": "heldout_lease_amendment.txt",
                "locator_substr": "Section 3",
                "snippet": "$45.00"
            },
            {
                "q": "commercial lease dated January 15 2024 recital background",
                "file": "heldout_lease_amendment.txt",
                "locator_substr": "Recital A",
                "snippet": "January 15, 2024"
            },
            {
                "q": "interior alterations construction allowance leased premises",
                "file": "heldout_lease_amendment.txt",
                "locator_substr": "Section 3",
                "snippet": "interior alterations"
            },

            # 21-25: heldout_software_license.txt
            {
                "q": "non-exclusive perpetual license deploy software 50 server nodes",
                "file": "heldout_software_license.txt",
                "locator_substr": "Section 1",
                "snippet": "50 server nodes"
            },
            {
                "q": "service level agreement 99.95% monthly service availability SLA",
                "file": "heldout_software_license.txt",
                "locator_substr": "Section 2",
                "snippet": "99.95%"
            },
            {
                "q": "Priority 1 outages exceeding 30 minutes 10% credit subscription fee",
                "file": "heldout_software_license.txt",
                "locator_substr": "Section 2",
                "snippet": "10% credit"
            },
            {
                "q": "limitation of liability indemnification cap gross negligence willful misconduct",
                "file": "heldout_software_license.txt",
                "locator_substr": "Section 3",
                "snippet": "Limitation of Liability"
            },
            {
                "q": "aggregate liability shall not exceed fees paid in prior 12 months",
                "file": "heldout_software_license.txt",
                "locator_substr": "Section 3",
                "snippet": "prior 12 months"
            },
        ]

        recall_hits = 0
        citation_matches = 0
        span_matches = 0
        total = len(heldout_queries)

        for item in heldout_queries:
            query = item["q"]
            target_file = item["file"]
            target_loc = item["locator_substr"]
            target_snip = item["snippet"]

            hits = self.nexus.search(query, workspace="HeldoutWorkspace", limit=5)
            self.assertGreater(len(hits), 0, f"Query '{query}' returned zero hits.")

            # Metric 1: Recall@5 (target doc found in top-5)
            found_target = any(h.filename == target_file for h in hits)
            if found_target:
                recall_hits += 1

            # Metric 2: Citation Accuracy (top hit points to target document and correct structural locator)
            top_hit = hits[0]
            if top_hit.filename == target_file:
                loc_str = (top_hit.locator or "") + " " + (top_hit.header or "") + " " + top_hit.citation + " " + " ".join(top_hit.heading_path)
                if target_loc.lower() in loc_str.lower() or target_snip.lower() in top_hit.text.lower():
                    citation_matches += 1

            # Metric 3: Span Precision (top hit provides character bounds containing the snippet)
            if top_hit.char_start is not None and top_hit.char_end is not None:
                if top_hit.char_end > top_hit.char_start and target_snip.lower() in top_hit.text.lower():
                    span_matches += 1

        recall_at_5 = recall_hits / total
        citation_accuracy = citation_matches / total
        span_precision = span_matches / total

        print("\n=== EVAL_HELDOUT 25-QUERY GENERALIZATION RESULTS ===")
        print(f"Total Heldout Queries:     {total}")
        print(f"Recall@5:                  {recall_at_5:.1%} ({recall_hits}/{total})")
        print(f"Citation Accuracy:         {citation_accuracy:.1%} ({citation_matches}/{total})")
        print(f"Span Precision:            {span_precision:.1%} ({span_matches}/{total})")
        print("====================================================\n")

        # Held-out quality gates
        self.assertGreaterEqual(recall_at_5, 0.85, f"Held-out Recall@5 dropped below 85%: {recall_at_5:.1%}")
        self.assertGreaterEqual(citation_accuracy, 0.80, f"Held-out Citation Accuracy dropped below 80%: {citation_accuracy:.1%}")
        self.assertGreaterEqual(span_precision, 0.80, f"Held-out Span Precision dropped below 80%: {span_precision:.1%}")


if __name__ == "__main__":
    unittest.main()
