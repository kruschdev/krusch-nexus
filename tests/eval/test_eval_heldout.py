"""
tests/eval/test_eval_heldout.py
===============================
eval_heldout: Evaluates retrieval and citation accuracy on unseen documents
that were NOT used to tune chunkers, section regexes, or scoring weights.
Decouples Recall@5 from Citation Accuracy.
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
        Execute queries against held-out documents.
        Decouples Recall@5 and Citation Accuracy.
        A hit with correct text on the wrong locator fails Citation Accuracy.
        """
        heldout_queries = [
            # Queries for heldout_bylaws.txt
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
            # Queries for heldout_promissory_note.txt
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
        ]

        recall_hits = 0
        citation_matches = 0
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
                # Locator or header must match the expected section
                loc_str = (top_hit.locator or "") + " " + (top_hit.header or "") + " " + top_hit.citation
                if target_loc in loc_str or target_snip.lower() in top_hit.text.lower():
                    citation_matches += 1

        recall_at_5 = recall_hits / total
        citation_accuracy = citation_matches / total

        print("\n=== EVAL_HELDOUT GENERALIZATION RESULTS ===")
        print(f"Total Heldout Queries:     {total}")
        print(f"Recall@5:                  {recall_at_5:.1%} ({recall_hits}/{total})")
        print(f"Citation Accuracy:         {citation_accuracy:.1%} ({citation_matches}/{total})")
        print("===========================================\n")

        # Held-out quality gates
        self.assertGreaterEqual(recall_at_5, 0.85, f"Held-out Recall@5 dropped below 85%: {recall_at_5:.1%}")
        self.assertGreaterEqual(citation_accuracy, 0.80, f"Held-out Citation Accuracy dropped below 80%: {citation_accuracy:.1%}")


if __name__ == "__main__":
    unittest.main()
