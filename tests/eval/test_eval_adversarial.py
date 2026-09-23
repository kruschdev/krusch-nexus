"""
tests/eval/test_eval_adversarial.py
===================================
eval_adversarial: Stress-tests KruschNexus on complex and adversarial document structures:
- Real Two-column statutory PDFs (multi-column layout without column mingling)
- Real Redline PDFs with strikethroughs and visual revision additions
- Real Scanned Fax PDFs with transmission headers, noise, and FILED stamps
- Empty OCR scans (empty page handling & warnings)
- Encrypted PDF fail-closed defense
- OCR Benchmark reporting Character Error Rate (CER) and Word Error Rate (WER)
"""

import os
import shutil
import tempfile
import unittest
from typing import List, Dict, Any, Optional

from krusch_nexus import NexusClient, NexusConfig, DocType, WarningCode
from krusch_nexus.store import init_db, get_engine
from krusch_nexus.exceptions import EncryptedPdfError
from krusch_nexus.parsers.ocr import try_tesseract_ocr

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")


def compute_cer(reference: str, hypothesis: str) -> float:
    """Character Error Rate using Levenshtein distance."""
    ref = reference.strip()
    hyp = hypothesis.strip()
    if not ref:
        return 0.0 if not hyp else 1.0
    dp = list(range(len(hyp) + 1))
    for i, r_char in enumerate(ref, 1):
        new_dp = [i] + [0] * len(hyp)
        for j, h_char in enumerate(hyp, 1):
            cost = 0 if r_char.lower() == h_char.lower() else 1
            new_dp[j] = min(dp[j] + 1, new_dp[j - 1] + 1, dp[j - 1] + cost)
        dp = new_dp
    return min(1.0, dp[-1] / float(len(ref)))


def compute_wer(reference: str, hypothesis: str) -> float:
    """Word Error Rate using Levenshtein distance on normalized tokens."""
    ref_words = [w.lower().strip(".,;:!?()[]$\"'") for w in reference.split() if w.strip(".,;:!?()[]$\"'")]
    hyp_words = [w.lower().strip(".,;:!?()[]$\"'") for w in hypothesis.split() if w.strip(".,;:!?()[]$\"'")]
    if not ref_words:
        return 0.0 if not hyp_words else 1.0
    dp = list(range(len(hyp_words) + 1))
    for i, r_word in enumerate(ref_words, 1):
        new_dp = [i] + [0] * len(hyp_words)
        for j, h_word in enumerate(hyp_words, 1):
            cost = 0 if r_word == h_word else 1
            new_dp[j] = min(dp[j] + 1, new_dp[j - 1] + 1, dp[j - 1] + cost)
        dp = new_dp
    return min(1.0, dp[-1] / float(len(ref_words)))


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
            ("adversarial_twocolumn.pdf", DocType.AUTHORITY),
            ("adversarial_redline.pdf", DocType.WORK_PRODUCT),
            ("adversarial_fax_stamp.pdf", DocType.AUTHORITY),
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

    def test_twocolumn_pdf_retrieval(self):
        """Verify real two-column PDF retains column definitions and terms."""
        hits = self.nexus.search("Section 9.102 Definitions Accession goods physically united", workspace="AdversarialWorkspace", limit=3)
        self.assertGreater(len(hits), 0)
        self.assertTrue(any(h.filename == "adversarial_twocolumn.pdf" for h in hits))
        self.assertTrue(any("Accession" in h.text for h in hits), "Top hits must include Accession definition chunk")

    def test_redline_pdf_retrieval(self):
        """Verify real redlined PDF with strikethroughs retrieves amended provision."""
        hits = self.nexus.search("Section 4.1 Settlement Consideration $1,250,000", workspace="AdversarialWorkspace", limit=3)
        self.assertGreater(len(hits), 0)
        top = hits[0]
        self.assertEqual(top.filename, "adversarial_redline.pdf")
        self.assertTrue("1250000" in top.text or "1,250,000" in top.text)
        self.assertIn("Settlement Consideration", top.text)

    def test_fax_stamp_pdf_retrieval(self):
        """Verify real scanned fax PDF with noise and red stamp retrieves indemnification terms."""
        hits = self.nexus.search("Section 12.4 Indemnification and Defense Obligations fifty thousand dollars", workspace="AdversarialWorkspace", limit=3)
        self.assertGreater(len(hits), 0)
        top = hits[0]
        self.assertEqual(top.filename, "adversarial_fax_stamp.pdf")
        self.assertIn("Indemnification", top.text)

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

    def test_ocr_benchmark_cer_and_wer(self):
        """
        Benchmark OCR character and word accuracy on actual scanned PDF pages.
        Measures CER and WER against ground truth transcripts.
        """
        ocr_cases = [
            {
                "file": "scanned_page.pdf",
                "page": 1,
                "ref": (
                    "EXHIBIT B: SCANNED SETTLEMENT RELEASE Section 14.1 Liquidated Damages "
                    "The parties agree that liquidated damages shall be exactly fifty thousand dollars. "
                    "Executed this twenty-second day of September 2026."
                ),
                "max_cer": 0.15,
                "max_wer": 0.45
            },
            {
                "file": "adversarial_fax_stamp.pdf",
                "page": 1,
                "ref": (
                    "CONFIDENTIAL SETTLEMENT RELEASE AND COVENANT NOT TO SUE "
                    "Section 12.4 Indemnification and Defense Obligations "
                    "Indemnifying party agrees to defend, indemnify, and hold harmless all indemnitees. "
                    "Section 12.5 Limitation of Liability "
                    "Total aggregate liability shall not exceed fifty thousand dollars ($50,000)."
                ),
                "max_cer": 0.45,
                "max_wer": 0.65
            }
        ]

        print("\n=== OCR BENCHMARK CER / WER EVALUATION ===")
        for case in ocr_cases:
            pdf_path = os.path.join(FIXTURES_DIR, case["file"])
            extracted_text, conf, _ = try_tesseract_ocr(pdf_path, case["page"])
            self.assertIsNotNone(extracted_text, f"OCR returned None for {case['file']}")

            cer = compute_cer(case["ref"], extracted_text)
            wer = compute_wer(case["ref"], extracted_text)

            print(f"File: {case['file']} (p.{case['page']})")
            print(f"  Confidence: {conf:.2f}" if conf else "  Confidence: N/A")
            print(f"  CER:        {cer:.2%}")
            print(f"  WER:        {wer:.2%}")

            self.assertLessEqual(cer, case["max_cer"], f"CER for {case['file']} exceeded {case['max_cer']:.1%}: {cer:.2%}")
            self.assertLessEqual(wer, case["max_wer"], f"WER for {case['file']} exceeded {case['max_wer']:.1%}: {wer:.2%}")
        print("==========================================\n")


if __name__ == "__main__":
    unittest.main()
