"""
tests/eval/test_eval_adversarial.py
===================================
eval_adversarial: Stress-tests KruschNexus on complex and adversarial document structures:
- Two-column statutes (avoid cross-column merge)
- Redline text with strikethroughs and additions
- Empty OCR scans (empty page handling & warnings)
- Encrypted PDF fail-closed defense
"""

import os
import shutil
import tempfile
import unittest

from krusch_nexus import NexusClient, NexusConfig, DocType, WarningCode
from krusch_nexus.store import init_db, get_engine
from krusch_nexus.exceptions import EncryptedPdfError

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")


class TestEvalAdversarial(unittest.TestCase):
    """
    eval_adversarial suite:
    Verifies parser, chunker, and retriever resilience against difficult real-world documents.
    """

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="nexus_eval_adversarial_")
        cls.db_path = os.path.join(cls.temp_dir, "adversarial.db")
        cls.config = NexusConfig(
            database_url=f"sqlite:///{cls.db_path}",
            ocr_threshold_chars=40,
            ocr_dpi=300,
            allowed_ingest_roots=[FIXTURES_DIR, cls.temp_dir]
        )
        cls.engine = get_engine(cls.config.database_url)
        init_db(cls.engine)
        cls.nexus = NexusClient(cls.config)

        # Ingest adversarial corpus into "AdversarialWorkspace"
        cls.adversarial_files = [
            ("adversarial_twocolumn.txt", DocType.AUTHORITY),
            ("adversarial_redline.txt", DocType.WORK_PRODUCT),
            ("adversarial_blank_scan.pdf", DocType.AUTHORITY),
        ]

        cls.reports = {}
        for fname, doc_type in cls.adversarial_files:
            path = os.path.join(FIXTURES_DIR, fname)
            report = cls.nexus.ingest(
                filepath=path,
                workspace="AdversarialWorkspace",
                doc_type=doc_type,
                archive=False
            )
            cls.reports[fname] = report

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_twocolumn_statute_retrieval(self):
        """Verify two-column tabular statute retains term associations without corrupting definitions."""
        hits = self.nexus.search("Section 9-102 Accession goods physically united", workspace="AdversarialWorkspace", limit=3)
        self.assertGreater(len(hits), 0)
        self.assertTrue(any(h.filename == "adversarial_twocolumn.txt" for h in hits))
        self.assertTrue(any("Accession" in h.text for h in hits), "Top hits must include Accession definition chunk")

    def test_redline_contract_retrieval(self):
        """Verify redlined contract allows targeting of both original and amended provisions."""
        hits = self.nexus.search("settlement consideration $1,250,000 strict confidence", workspace="AdversarialWorkspace", limit=3)
        self.assertGreater(len(hits), 0)
        top = hits[0]
        self.assertEqual(top.filename, "adversarial_redline.txt")
        self.assertIn("$1,250,000", top.text)
        self.assertIn("Article 4", top.text)

    def test_blank_scan_empty_ocr_warning(self):
        """Verify empty scanned image fails safely with informative error and no corrupted chunks."""
        blank_rep = self.reports.get("adversarial_blank_scan.pdf")
        self.assertIsNotNone(blank_rep)
        self.assertEqual(blank_rep.status, "failed")
        self.assertIn("No usable content chunks", blank_rep.error)

    def test_encrypted_pdf_fail_closed_rejection(self):
        """Verify encrypted PDF is rejected fail-closed without corrupting the corpus."""
        encrypted_path = os.path.join(FIXTURES_DIR, "encrypted_sample.pdf")
        report = self.nexus.ingest(
            filepath=encrypted_path,
            workspace="AdversarialWorkspace",
            doc_type=DocType.AUTHORITY
        )
        self.assertEqual(report.status, "failed")
        self.assertIn("EncryptedPdfError", report.error)


if __name__ == "__main__":
    unittest.main()
