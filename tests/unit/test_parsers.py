"""
Unit tests for KruschNexus hardened parsers.
Verifies:
- DOCX in-order table extraction (table appears between paragraphs, not appended)
- HTML stdlib parsing (headings, paragraphs, lists, tables)
- EML RFC2047 MIME header decoding
- PDF multi-page fidelity
"""

import os
import tempfile
import unittest
from krusch_nexus.parsers import (
    parse_docx,
    parse_html,
    parse_eml,
    parse_pdf,
    parse_plain_or_code,
    extract_html_text
)

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")


class TestParsers(unittest.TestCase):

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

        # Verify heading 1 and heading 2 are present
        self.assertIn("# Section 1: Fleet Information Security Policy", text)
        self.assertIn("## Section 1.2: Backup Retention Standards", text)

        # In policy_manual.docx, the sequence is:
        # 1. "Document archives in .ingested/ must be retained according to the following schedule:"
        # 2. Table: "| Document Class | Minimum Retention Period |" ... "| Privileged Corporate Paper | Seven (7) Years |"
        # 3. Following paragraph: "Purging of records prior to the expiration of seven years constitutes a policy violation."
        idx_lead_paragraph = text.find("according to the following schedule")
        idx_table = text.find("Privileged Corporate Paper")
        idx_trailing_paragraph = text.find("Purging of records prior to the expiration")

        self.assertNotEqual(idx_lead_paragraph, -1, "Leading paragraph must be found")
        self.assertNotEqual(idx_table, -1, "Table row must be found")
        self.assertNotEqual(idx_trailing_paragraph, -1, "Trailing paragraph must be found")

        # ASSERT STRICT DOCUMENT ORDER: lead paragraph < table < trailing paragraph
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

        # Style tags must be stripped
        self.assertNotIn("color: red", text)
        self.assertNotIn("style", text)

        # Headings converted to Markdown
        self.assertIn("# Legal Memorandum", text)
        self.assertIn("## Statutory Analysis", text)

        # Bullet list converted
        self.assertIn("- Factor A", text)
        self.assertIn("- Factor B", text)

        # Table converted
        self.assertIn("| Header 1 | Header 2 |", text)
        self.assertIn("| Val 1 | Val 2 |", text)
        self.assertIn("Final conclusion.", text)

    def test_eml_mime_header_decoding(self):
        """Verify RFC2047 MIME encoded words in Subject and From are decoded into clean text."""
        eml_path = os.path.join(FIXTURES_DIR, "deal_memo.eml")
        doc = parse_eml(eml_path, "deal_memo.eml")

        self.assertEqual(doc.total_pages, 1)
        text = doc.pages[0].text

        # Headers must be cleanly decoded (not raw =?utf-8?B?...?=)
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
