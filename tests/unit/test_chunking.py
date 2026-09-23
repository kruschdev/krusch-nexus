"""
Unit tests for KruschNexus structure-aware chunking.
Verifies heading breadcrumb propagation, sentence splitting, and overlap preservation.
"""

import unittest
from krusch_nexus.parsers import ParsedPage
from krusch_nexus.chunking import chunk_document_pages, detect_header_candidate


class TestChunking(unittest.TestCase):

    def test_detect_header_candidates(self):
        """Verify statutory, markdown, and uppercase heading detection."""
        self.assertEqual(detect_header_candidate("# Heading One"), "Heading One")
        self.assertEqual(detect_header_candidate("Section 8.22 Permitted Use"), "Section 8.22 Permitted Use")
        self.assertEqual(detect_header_candidate("§ 1950.5 Security Deposits"), "§ 1950.5 Security Deposits")
        self.assertEqual(detect_header_candidate("Article 4 Termination"), "Article 4 Termination")
        self.assertEqual(detect_header_candidate("TERMINATION OF LEASE"), "Termination Of Lease")
        self.assertIsNone(detect_header_candidate("This is a normal paragraph sentence."))

    def test_chunk_page_fidelity_and_breadcrumbs(self):
        """Verify chunks preserve 1-based page numbers and prepend heading breadcrumbs."""
        page_content = (
            "Section 1.2: Backup Retention Standards\n\n"
            "This is the first paragraph discussing retention schedules.\n\n"
            "This is the second paragraph confirming seven year minimums."
        )
        pages = [
            ParsedPage(page_number=1, text=page_content),
            ParsedPage(page_number=2, text="Section 2.0: Hardware Audits\n\nHardware must be audited quarterly.")
        ]

        chunks = chunk_document_pages(
            pages=pages,
            filename="policy.docx",
            file_hash="mock_hash_123",
            max_chars=2000,
            overlap_chars=100
        )

        self.assertGreaterEqual(len(chunks), 2)
        # Page 1 check
        self.assertEqual(chunks[0].page_number, 1)
        self.assertIn("Section 1.2: Backup Retention Standards", chunks[0].text)
        self.assertIn("Section 1.2: Backup Retention Standards", chunks[0].citation)
        self.assertNotIn("[policy.docx", chunks[0].text)

        # Page 2 check
        p2_chunks = [c for c in chunks if c.page_number == 2]
        self.assertTrue(len(p2_chunks) > 0)
        self.assertEqual(p2_chunks[0].page_number, 2)
        self.assertIn("Section 2.0: Hardware Audits", p2_chunks[0].text)
        self.assertIn("Section 2.0: Hardware Audits", p2_chunks[0].citation)
        self.assertNotIn("[policy.docx", p2_chunks[0].text)

    def test_embedding_input_does_not_contain_citation_string(self):
        """
        Critical Invariant:
        Embedding vector input (chunk.text) must NEVER contain baked-in citation
        strings such as '[filename - p.N] § ...' or synthetic formatting wrappers.
        Citations and breadcrumbs live strictly in metadata.
        """
        page = ParsedPage(page_number=14, text="Article IV: Governance\nSection 8.22.030: Operative text of statute.")
        chunks = chunk_document_pages(
            pages=[page],
            filename="ordinance.pdf",
            file_hash="hash_abc_999",
            max_chars=1000
        )
        self.assertGreaterEqual(len(chunks), 1)
        for c in chunks:
            # Text to be embedded must never contain citation wrappers
            self.assertNotEqual(c.text, c.citation)
            self.assertNotIn("ordinance.pdf", c.text)
            self.assertNotIn("p.14", c.text)
            self.assertNotIn("[ordinance.pdf", c.text)

            # Formatted citation must contain document and page number
            self.assertIn("ordinance.pdf", c.citation)
            self.assertIn("p.14", c.citation)

            # Structured locator and heading path
            self.assertIsNotNone(c.structured_locator)
            self.assertEqual(c.structured_locator.page, 14)
            self.assertIsInstance(c.heading_path, list)
            self.assertIn("Page 14", c.heading_path)


if __name__ == "__main__":
    unittest.main()
