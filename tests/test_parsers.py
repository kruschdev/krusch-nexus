"""
Unit Tests for KruschNexus Parsers
==================================
Tests multi-format file parsers without external file dependencies:
PDF (synthetic), DOCX (in-memory zip), EML (RFC822), HTML, CSV, JSON, Markdown.
Compatible with both standard unittest and pytest.
"""

import os
import tempfile
import zipfile
import unittest

from src.backend.parsers import (
    parse_document,
    parse_docx,
    parse_eml,
    parse_html,
    parse_plain_or_code,
    compute_file_hash,
    ParsedPage
)


class TestParsers(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="nexus_parser_test_")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_synthetic_pdf_parsing(self):
        """Verify PDF parser extracts text and preserves 1-based page numbers."""
        pdf_path = os.path.join(self.temp_dir.name, "contract.pdf")
        pdf_content = (
            b"%PDF-1.4\n"
            b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
            b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
            b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
            b"4 0 obj << /Length 44 >> stream\n"
            b"BT /F1 12 Tf 100 700 Td (Project Alpha Merger Agreement) Tj ET\n"
            b"endstream\nendobj\n"
            b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
            b"xref\n0 6\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000266 00000 n \n0000000360 00000 n \n"
            b"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n437\n%%EOF\n"
        )
        with open(pdf_path, "wb") as f:
            f.write(pdf_content)

        docs = parse_document(pdf_path, "contract.pdf")
        self.assertGreaterEqual(len(docs), 1)
        self.assertEqual(docs[0].metadata["page_number"], 1)
        self.assertEqual(docs[0].metadata["filename"], "contract.pdf")
        self.assertEqual(len(docs[0].metadata["file_hash"]), 64)
        self.assertIn("Project Alpha Merger Agreement", docs[0].text)

    def test_docx_xml_parsing(self):
        """Verify DOCX parser parses paragraphs and headings directly from XML."""
        docx_path = os.path.join(self.temp_dir.name, "test_doc.docx")
        xml_data = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            <w:p>
              <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
              <w:r><w:t>Section 1: Executive Scope</w:t></w:r>
            </w:p>
            <w:p>
              <w:r><w:t>All systems operate air-gapped without external egress.</w:t></w:r>
            </w:p>
          </w:body>
        </w:document>
        """
        with zipfile.ZipFile(docx_path, "w") as zf:
            zf.writestr("word/document.xml", xml_data)

        parsed = parse_docx(docx_path, "test_doc.docx")
        self.assertEqual(parsed.total_pages, 1)
        self.assertIn("Section 1: Executive Scope", parsed.full_text)
        self.assertIn("air-gapped", parsed.full_text)

    def test_eml_parsing(self):
        """Verify EML parser parses RFC822 headers and body text."""
        eml_path = os.path.join(self.temp_dir.name, "deal.eml")
        eml_content = (
            "From: general.counsel@krusch.dev\n"
            "To: executive@krusch.dev\n"
            "Subject: Privileged - Acquisition Review\n"
            "Date: Tue, 22 Sep 2026 10:00:00 -0400\n"
            "Content-Type: text/plain; charset=utf-8\n"
            "\n"
            "This matter is covered under attorney-client privilege. Review terms attached."
        )
        with open(eml_path, "w") as f:
            f.write(eml_content)

        parsed = parse_eml(eml_path, "deal.eml")
        self.assertEqual(parsed.total_pages, 1)
        self.assertIn("Subject: Privileged - Acquisition Review", parsed.full_text)
        self.assertIn("attorney-client privilege", parsed.full_text)

    def test_html_parsing(self):
        """Verify HTML parser strips tags and formats headings as Markdown."""
        html_path = os.path.join(self.temp_dir.name, "policy.html")
        html_content = (
            "<html><body>"
            "<h1>Corporate Governance Policy</h1>"
            "<p>All employees must adhere to zero-retention policies.</p>"
            "<h2>Enforcement</h2>"
            "<p>Audits occur quarterly.</p>"
            "</body></html>"
        )
        with open(html_path, "w") as f:
            f.write(html_content)

        parsed = parse_html(html_path, "policy.html")
        self.assertIn("# Corporate Governance Policy", parsed.full_text)
        self.assertIn("## Enforcement", parsed.full_text)
        self.assertIn("zero-retention", parsed.full_text)

    def test_csv_json_markdown_parsing(self):
        """Verify plain text, markdown, CSV, and JSON parsing."""
        # CSV
        csv_path = os.path.join(self.temp_dir.name, "table.csv")
        with open(csv_path, "w") as f:
            f.write("Department,Budget,Lead\nLegal,500000,Sarah\nEngineering,1200000,Alex\n")
        parsed_csv = parse_plain_or_code(csv_path, "table.csv")
        self.assertIn("Department | Budget | Lead", parsed_csv.full_text)

        # JSON
        json_path = os.path.join(self.temp_dir.name, "config.json")
        with open(json_path, "w") as f:
            f.write('{"node": "kruschserv", "status": "active", "gpu": "RTX 2080 Ti"}')
        parsed_json = parse_plain_or_code(json_path, "config.json")
        self.assertIn("kruschserv", parsed_json.full_text)

        # Markdown
        md_path = os.path.join(self.temp_dir.name, "notes.md")
        with open(md_path, "w") as f:
            f.write("# Meeting Notes\n- Task 1\n- Task 2")
        parsed_md = parse_plain_or_code(md_path, "notes.md")
        self.assertIn("# Meeting Notes", parsed_md.full_text)

    def test_compute_file_hash(self):
        """Verify SHA-256 deduplication hashing."""
        test_path = os.path.join(self.temp_dir.name, "hash_test.txt")
        with open(test_path, "w") as f:
            f.write("Deterministic hash content")
        h1 = compute_file_hash(test_path)
        h2 = compute_file_hash(test_path)
        self.assertEqual(len(h1), 64)
        self.assertEqual(h1, h2)


if __name__ == "__main__":
    unittest.main()
