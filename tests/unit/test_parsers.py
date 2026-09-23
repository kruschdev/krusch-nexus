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
    parse_html,
    parse_eml,
    parse_pdf,
    parse_plain_or_code,
    extract_html_text,
    detect_file_mime
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


if __name__ == "__main__":
    unittest.main()
