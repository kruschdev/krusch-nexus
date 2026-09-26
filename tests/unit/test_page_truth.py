"""
tests/unit/test_page_truth.py
=============================
First-class Page Truth Verification Suite.
Guarantees:
1. Physical PDF 1-based page index (pdf_page) is never confused with printed folios (printed_page).
2. After cover pages, TOCs, and mid-PDF scanned exhibits, page_number references the physical PDF page that users open in a PDF viewer.
3. Both pdf_page and printed_page are stored on SearchHit, Citation, and StructuredLocator.
4. DOCX and unpaged formats strictly maintain page_number=None (no synthetic pages).
"""

import os
import shutil
import tempfile
import unittest

from krusch_nexus import NexusClient, NexusConfig, DocType, format_citation, StructuredLocator
from krusch_nexus.store import init_db, get_engine

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")


class TestPageTruth(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="nexus_page_truth_")
        cls.db_path = os.path.join(cls.temp_dir, "page_truth.db")
        cls.config = NexusConfig(
            database_url=f"sqlite:///{cls.db_path}",
            allowed_ingest_roots=[FIXTURES_DIR, cls.temp_dir]
        )
        cls.engine = get_engine(cls.config.database_url)
        init_db(cls.engine)
        cls.nexus = NexusClient(cls.config)

        # Ingest mixed PDF where physical and printed pages diverge
        mixed_pdf = os.path.join(FIXTURES_DIR, "heldout_mixed_digital_scan.pdf")
        policy_docx = os.path.join(FIXTURES_DIR, "policy_manual.docx")

        cls.rep_mixed = cls.nexus.ingest(mixed_pdf, workspace="PageTruthWS", doc_type=DocType.AUTHORITY)
        cls.rep_docx = cls.nexus.ingest(policy_docx, workspace="PageTruthWS", doc_type=DocType.WORK_PRODUCT)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_divergent_printed_vs_physical_page_indices(self):
        """
        Verify physical coordinate system truth:
        In heldout_mixed_digital_scan.pdf:
        - Page 1: Cover Page (physical page 1)
        - Page 2: Table of Contents (physical page 2, printed 'Page ii')
        - Page 3: Operative Lease (physical page 3, printed 'Page 2 of 10')
        - Page 4: Scanned Exhibit (physical page 4, printed 'Exhibit B-1')
        """
        # Query matching page 3
        hits_rent = self.nexus.search(
            "monthly base rent $42,500 due on first day",
            workspace="PageTruthWS",
            limit=3
        )
        self.assertGreater(len(hits_rent), 0)
        hit_rent = hits_rent[0]

        # Invariant 1: page_number and pdf_page MUST reference physical page 3
        self.assertEqual(hit_rent.page_number, 3, "Hit on Page 3 must have page_number=3 (physical PDF index)")
        self.assertEqual(hit_rent.pdf_page, 3, "Hit on Page 3 must have pdf_page=3")

        # Invariant 2: Citation string must open physical page 3
        self.assertIn("p.3", hit_rent.citation)
        self.assertNotIn("p.2", hit_rent.citation)
        self.assertNotIn("p. None", hit_rent.citation)

        # Invariant 3: StructuredLocator preserves physical index and locator
        self.assertIsNotNone(hit_rent.structured_locator)
        self.assertEqual(hit_rent.structured_locator.page, 3)
        self.assertEqual(hit_rent.structured_locator.pdf_page, 3)

        # Query matching page 4 (scanned exhibit inserted mid-document)
        hits_exhibit = self.nexus.search(
            "Schedule of Permitted Subtenants Apex Logistics occupancy Suite 400",
            workspace="PageTruthWS",
            limit=3
        )
        self.assertGreater(len(hits_exhibit), 0)
        hit_exhibit = hits_exhibit[0]

        # Invariant 4: Scanned exhibit on physical page 4 must have page_number=4
        self.assertEqual(hit_exhibit.page_number, 4, "Scanned exhibit must have page_number=4 (physical PDF index)")
        self.assertEqual(hit_exhibit.pdf_page, 4)
        self.assertIn("p.4", hit_exhibit.citation)

    def test_docx_honest_unpaged_truth(self):
        """
        Verify DOCX documents never invent synthetic page coordinates.
        page_number and pdf_page must be strictly None.
        """
        hits = self.nexus.search(
            "privileged corporate paper minimum retention period seven years",
            workspace="PageTruthWS",
            limit=3
        )
        self.assertGreater(len(hits), 0)
        hit = hits[0]

        self.assertIsNone(hit.page_number, "DOCX hit must have page_number=None")
        self.assertIsNone(hit.pdf_page, "DOCX hit must have pdf_page=None")
        self.assertNotIn("p. None", hit.citation)
        self.assertNotIn("p.", hit.citation)
        self.assertIn("Section 1", hit.citation)

    def test_citation_formatting_with_printed_page(self):
        """Verify format_citation displays both physical page and printed label when divergent."""
        # When physical page 5 has printed folio 'Page 1'
        cit_divergent = format_citation(
            filename="brief.pdf",
            page_number=5,
            pdf_page=5,
            printed_page="Page 1",
            header="Argument I"
        )
        self.assertIn("p.5", cit_divergent)
        self.assertIn("[printed: Page 1]", cit_divergent)
        self.assertIn("Argument I", cit_divergent)

        # When physical page and printed page match, no redundant label
        cit_same = format_citation(
            filename="brief.pdf",
            page_number=5,
            pdf_page=5,
            printed_page="5",
            header="Argument I"
        )
        self.assertIn("p.5", cit_same)
        self.assertNotIn("[printed:", cit_same)


if __name__ == "__main__":
    unittest.main()
