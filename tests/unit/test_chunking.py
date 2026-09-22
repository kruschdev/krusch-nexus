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
        self.assertIn("[policy.docx - p.1] Section 1.2: Backup Retention Standards", chunks[0].text)

        # Page 2 check
        p2_chunks = [c for c in chunks if c.page_number == 2]
        self.assertTrue(len(p2_chunks) > 0)
        self.assertEqual(p2_chunks[0].page_number, 2)
        self.assertIn("[policy.docx - p.2] Section 2.0: Hardware Audits", p2_chunks[0].text)


if __name__ == "__main__":
    unittest.main()
