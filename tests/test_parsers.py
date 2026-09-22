"""
Unit Tests for KruschNexus Parsers & Local OCR Fallback
======================================================
Tests multi-format file parsers and verifies Tesseract OCR fallback on scanned PDFs.
"""

import os
import unittest

from src.backend.parsers import (
    parse_document,
    parse_pdf,
    parse_docx,
    parse_eml,
    parse_html,
    parse_plain_or_code,
    compute_file_hash,
    ParsedPage
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


class TestParsers(unittest.TestCase):

    def test_scanned_pdf_ocr_fallback(self):
        """Verify that an image-only scanned PDF automatically triggers local Tesseract OCR."""
        pdf_path = os.path.join(FIXTURES_DIR, "scanned_page.pdf")
        self.assertTrue(os.path.exists(pdf_path), "Missing scanned_page.pdf fixture")

        docs = parse_document(pdf_path, "scanned_page.pdf", ocr_threshold=30, ocr_dpi=150)
        self.assertEqual(len(docs), 1)
        self.assertTrue(docs[0].metadata.get("ocr_applied"), "OCR should have been applied to scanned PDF")
        self.assertEqual(docs[0].metadata.get("page_number"), 1)
        self.assertIn("Liquidated Damages", docs[0].text)

    def test_sample_contract_pdf_multipage(self):
        """Verify layout-preserving multi-page PDF parser preserves exact 1-based page numbers."""
        pdf_path = os.path.join(FIXTURES_DIR, "sample_contract.pdf")
        self.assertTrue(os.path.exists(pdf_path))

        docs = parse_document(pdf_path, "sample_contract.pdf")
        self.assertEqual(len(docs), 2)
        self.assertEqual(docs[0].metadata["page_number"], 1)
        self.assertEqual(docs[1].metadata["page_number"], 2)
        self.assertIn("COMMERCIAL LEASE AGREEMENT", docs[0].text)
        self.assertIn("Termination for Breach", docs[1].text)

    def test_docx_parsing_with_headings(self):
        """Verify DOCX parser extracts paragraphs and Heading styles from document.xml."""
        docx_path = os.path.join(FIXTURES_DIR, "policy_manual.docx")
        self.assertTrue(os.path.exists(docx_path))

        docs = parse_document(docx_path, "policy_manual.docx")
        self.assertEqual(len(docs), 1)
        self.assertIn("# Section 1: Fleet Information Security Policy", docs[0].text)
        self.assertIn("## Section 1.2: Backup Retention Standards", docs[0].text)
        self.assertIn("air-gapped", docs[0].text)

    def test_eml_rfc822_parsing(self):
        """Verify EML parser parses standard headers and email body."""
        eml_path = os.path.join(FIXTURES_DIR, "deal_memo.eml")
        self.assertTrue(os.path.exists(eml_path))

        docs = parse_document(eml_path, "deal_memo.eml")
        self.assertEqual(len(docs), 1)
        self.assertIn("Subject: Privileged - Acquisition Review Protocol", docs[0].text)
        self.assertIn("From: general.counsel@krusch.dev", docs[0].text)
        self.assertIn("Purchase Agreement", docs[0].text)

    def test_municipal_code_statutory_text(self):
        """Verify plain text and statutory code with section symbols parses cleanly."""
        code_path = os.path.join(FIXTURES_DIR, "municipal_code.txt")
        self.assertTrue(os.path.exists(code_path))

        docs = parse_document(code_path, "municipal_code.txt")
        self.assertEqual(len(docs), 1)
        self.assertIn("§ 1950.5", docs[0].text)
        self.assertIn("Section 8.22.030", docs[0].text)


if __name__ == "__main__":
    unittest.main()
