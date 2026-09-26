"""
Unit tests for KruschNexus hardened parsers.
Verifies:
- DOCX in-order table extraction (table appears between paragraphs, not appended)
- HTML stdlib parsing (headings, paragraphs, lists, tables)
- EML RFC2047 MIME header decoding
- PDF multi-page fidelity
- True MIME / signature detection from magic bytes
"""

import os
import unittest
from krusch_nexus.parsers import (
    parse_docx,
    parse_eml,
    parse_pdf,
    extract_html_text,
    detect_file_mime,
    OCRPolicy
)

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")


class TestParsers(unittest.TestCase):

    def test_detect_file_mime_from_magic_bytes(self):
        """Verify detect_file_mime detects real MIME type from file header bytes."""
        pdf_path = os.path.join(FIXTURES_DIR, "sample_contract.pdf")
        self.assertEqual(detect_file_mime(pdf_path, "sample_contract.pdf"), "application/pdf")

        docx_path = os.path.join(FIXTURES_DIR, "policy_manual.docx")
        self.assertEqual(detect_file_mime(docx_path, "policy_manual.docx"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        eml_path = os.path.join(FIXTURES_DIR, "deal_memo.eml")
        self.assertEqual(detect_file_mime(eml_path, "deal_memo.eml"), "message/rfc822")

        csv_path = os.path.join(FIXTURES_DIR, "vendor_matrix.csv")
        self.assertEqual(detect_file_mime(csv_path, "vendor_matrix.csv"), "text/csv")

    def test_docx_in_order_table_extraction(self):
        """
        Verify DOCX parser emits table rows in document order between paragraphs,
        rather than dumping all tables at the end of the document.
        """
        docx_path = os.path.join(FIXTURES_DIR, "policy_manual.docx")
        self.assertTrue(os.path.exists(docx_path), "policy_manual.docx fixture must exist")

        doc = parse_docx(docx_path, "policy_manual.docx")
        self.assertEqual(doc.total_pages, 1)
        text = doc.pages[0].text

        self.assertIn("# Section 1: Fleet Information Security Policy", text)
        self.assertIn("## Section 1.2: Backup Retention Standards", text)

        idx_lead_paragraph = text.find("according to the following schedule")
        idx_table = text.find("Privileged Corporate Paper")
        idx_trailing_paragraph = text.find("Purging of records prior to the expiration")

        self.assertNotEqual(idx_lead_paragraph, -1, "Leading paragraph must be found")
        self.assertNotEqual(idx_table, -1, "Table row must be found")
        self.assertNotEqual(idx_trailing_paragraph, -1, "Trailing paragraph must be found")

        self.assertLess(
            idx_lead_paragraph,
            idx_table,
            "Table must appear AFTER the lead paragraph in document order"
        )
        self.assertLess(
            idx_table,
            idx_trailing_paragraph,
            "Table must appear BEFORE the trailing paragraph in document order (not dumped at the end!)"
        )

    def test_html_parser_stdlib(self):
        """Verify HTML parser converts semantic tags to markdown without nested regex."""
        html_sample = """
        <html>
            <head><title>Test Page</title><style>body { color: red; }</style></head>
            <body>
                <h1>Legal Memorandum</h1>
                <p>First paragraph detailing facts.</p>
                <h2>Statutory Analysis</h2>
                <ul>
                    <li>Factor A</li>
                    <li>Factor B</li>
                </ul>
                <table>
                    <tr><th>Header 1</th><th>Header 2</th></tr>
                    <tr><td>Val 1</td><td>Val 2</td></tr>
                </table>
                <p>Final conclusion.</p>
            </body>
        </html>
        """
        text = extract_html_text(html_sample)

        self.assertNotIn("color: red", text)
        self.assertNotIn("style", text)
        self.assertIn("# Legal Memorandum", text)
        self.assertIn("## Statutory Analysis", text)
        self.assertIn("- Factor A", text)
        self.assertIn("- Factor B", text)
        self.assertIn("| Header 1 | Header 2 |", text)
        self.assertIn("| Val 1 | Val 2 |", text)
        self.assertIn("Final conclusion.", text)

    def test_eml_mime_header_decoding(self):
        """Verify RFC2047 MIME encoded words in Subject and From are decoded into clean text."""
        eml_path = os.path.join(FIXTURES_DIR, "deal_memo.eml")
        doc = parse_eml(eml_path, "deal_memo.eml")

        self.assertEqual(doc.total_pages, 1)
        text = doc.pages[0].text

        self.assertIn("Subject: Privileged - Acquisition Review Protocol", text)
        self.assertIn("From: General Counsel <general.counsel@krusch.dev>", text)
        self.assertNotIn("=?utf-8?", text, "Raw MIME encoded-word tokens should not appear in parsed output")
        self.assertIn("Section 4.5 of the Purchase Agreement", text)

    def test_pdf_page_boundaries(self):
        """Verify multi-page PDF extracts correct page count and 1-based page numbers."""
        pdf_path = os.path.join(FIXTURES_DIR, "sample_contract.pdf")
        doc = parse_pdf(pdf_path, "sample_contract.pdf")

        self.assertEqual(doc.total_pages, 2)
        self.assertEqual(doc.pages[0].page_number, 1)
        self.assertEqual(doc.pages[1].page_number, 2)
        self.assertIn("Section 8.22 Permitted Use of Premises", doc.pages[0].text)
        self.assertIn("Section 19.3 Termination for Breach", doc.pages[1].text)

    def test_ocr_policy_defaults(self):
        """Verify unified OCRPolicy object values match documented standards."""
        policy = OCRPolicy()
        self.assertEqual(policy.min_printable_chars, 40)
        self.assertEqual(policy.dpi, 300)
        self.assertEqual(policy.psm_prose, 6)
        self.assertEqual(policy.psm_form, 4)
        self.assertEqual(policy.confidence_floor, 0.50)
        self.assertEqual(policy.language, "eng")
        self.assertEqual(policy.timeout_seconds, 30.0)

    def test_mixed_pdf_selective_ocr(self):
        """
        Verify that a mixed PDF (digital text pages + scanned exhibit)
        OCRs ONLY the scanned exhibit page, preserving digital text without overwriting.
        """
        mixed_path = os.path.join(FIXTURES_DIR, "mixed_sample.pdf")
        self.assertTrue(os.path.exists(mixed_path), "mixed_sample.pdf fixture must exist")

        policy = OCRPolicy(min_printable_chars=40, dpi=300)
        doc = parse_pdf(mixed_path, "mixed_sample.pdf", policy=policy)

        self.assertEqual(doc.total_pages, 3)
        self.assertEqual(doc.parser_version, "pdf-poppler@2.0")

        # Page 1: Digital text (no OCR applied)
        p1 = doc.pages[0]
        self.assertFalse(p1.ocr_applied, "Page 1 has digital text; OCR should not be applied")
        self.assertIsNone(p1.ocr_text, "Page 1 ocr_text should remain None")
        self.assertGreater(len(p1.digital_text), 0, "Page 1 digital_text must be populated")
        self.assertIn("COMMERCIAL LEASE AGREEMENT", p1.digital_text)

        # Page 2: Digital text (no OCR applied)
        p2 = doc.pages[1]
        self.assertFalse(p2.ocr_applied, "Page 2 has digital text; OCR should not be applied")
        self.assertIsNone(p2.ocr_text, "Page 2 ocr_text should remain None")
        self.assertGreater(len(p2.digital_text), 0, "Page 2 digital_text must be populated")
        self.assertIn("Termination for Breach", p2.digital_text)

        # Page 3: Scanned exhibit (OCR must be applied)
        p3 = doc.pages[2]
        self.assertTrue(p3.ocr_applied, "Page 3 is a scanned image; OCR must be applied")
        self.assertIsNotNone(p3.ocr_text, "Page 3 ocr_text must be populated")
        self.assertEqual(p3.digital_text, "", "Page 3 digital_text must remain empty (not corrupted by OCR)")
        self.assertIn("EXHIBIT", p3.ocr_text.upper())
        self.assertIn("SETTLEMENT RELEASE", p3.ocr_text.upper())
        self.assertIn("LIQUIDATED DAMAGES", p3.ocr_text.upper())

    def test_low_ocr_confidence_quarantine(self):
        """Verify that pages with mean OCR confidence below confidence_floor are quarantined."""
        from krusch_nexus import WarningCode
        scanned_path = os.path.join(FIXTURES_DIR, "scanned_page.pdf")
        policy = OCRPolicy(confidence_floor=0.99)
        doc = parse_pdf(scanned_path, "scanned_page.pdf", policy=policy)

        self.assertIn(WarningCode.LOW_OCR_CONFIDENCE.value, doc.warnings)
        self.assertFalse(doc.pages[0].ocr_applied)
        self.assertIn("low OCR confidence quarantined", doc.pages[0].text)

    def test_system_tool_versions_provenance(self):
        """Verify that parse_pdf attaches detected system tool versions (poppler, tesseract)."""
        pdf_path = os.path.join(FIXTURES_DIR, "sample_contract.pdf")
        doc = parse_pdf(pdf_path, "sample_contract.pdf")
        self.assertIsInstance(doc.tool_versions, dict)
        self.assertIn("poppler", doc.tool_versions)
        self.assertIn("tesseract", doc.tool_versions)


if __name__ == "__main__":
    unittest.main()
