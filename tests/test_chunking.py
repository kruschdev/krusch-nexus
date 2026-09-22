"""
Unit Tests for KruschNexus Chunking
===================================
Tests structure-aware document chunking, section header detection,
page boundary preservation, sliding-window overlap, and chunk deduplication.
Compatible with both standard unittest and pytest.
"""

import unittest
from src.backend.parsers import ParsedPage
from src.backend.chunking import (
    detect_header_candidate,
    chunk_document_pages,
    compute_chunk_hash,
    deduplicate_chunks,
    Chunk
)


class TestChunking(unittest.TestCase):

    def test_detect_header_candidate(self):
        """Verify heading candidate detection across markdown, legal citations, and all-caps titles."""
        self.assertEqual(detect_header_candidate("# Section 1 Introduction"), "Section 1 Introduction")
        self.assertEqual(detect_header_candidate("### 2.3 Data Security"), "2.3 Data Security")
        self.assertEqual(detect_header_candidate("§ 1950.5 Security Deposits"), "§ 1950.5 Security Deposits")
        self.assertEqual(detect_header_candidate("Section 4.1 Indemnification"), "Section 4.1 Indemnification")
        self.assertEqual(detect_header_candidate("CONFIDENTIALITY AGREEMENT"), "Confidentiality Agreement")
        # Long sentences should not be mistaken for headers
        self.assertIsNone(detect_header_candidate("This is a normal long paragraph describing the terms and conditions in detail."))

    def test_chunking_sliding_window_overlap(self):
        """Verify that overlap_chars preserves context across consecutive chunk boundaries."""
        sample_text = (
            "Alpha section begins with foundational premise.\n\n"
            "Beta section introduces the second critical operative clause for the transaction.\n\n"
            "Gamma section covers financial covenants and EBITDA calculations.\n\n"
            "Delta section concludes with dispute resolution and governing law."
        )
        page = ParsedPage(page_number=1, text=sample_text)
        # Set max_chars and overlap_chars to force chunk splits
        chunks = chunk_document_pages(
            pages=[page],
            filename="deal.md",
            file_hash="dummy_hash_123",
            max_chars=160,
            overlap_chars=90
        )

        self.assertGreaterEqual(len(chunks), 3, f"Expected at least 3 chunks, got {len(chunks)}")

        # Check that adjacent chunks share overlapping text
        chunk0_text = chunks[0].raw_text
        chunk1_text = chunks[1].raw_text
        chunk2_text = chunks[2].raw_text

        # Beta section should appear in both Chunk 0 and Chunk 1
        self.assertIn("Beta section", chunk0_text)
        self.assertIn("Beta section", chunk1_text)

        # Gamma section should appear in Chunk 1 and Chunk 2
        self.assertIn("Gamma section", chunk1_text)
        self.assertIn("Gamma section", chunk2_text)

    def test_chunk_page_provenance_and_breadcrumbs(self):
        """Verify each chunk retains exact 1-based page number and context breadcrumb prefix."""
        page1 = ParsedPage(page_number=1, text="### Overview\n\nWelcome to the firm documentation.")
        page2 = ParsedPage(page_number=2, text="### Retention Policy\n\nClient data must be retained for 7 years.")

        chunks = chunk_document_pages(
            pages=[page1, page2],
            filename="policy.pdf",
            file_hash="abc123456",
            max_chars=1000
        )

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].page_number == 1, True)
        self.assertEqual(chunks[0].header, "Overview")
        self.assertIn("[policy.pdf - p.1] Overview", chunks[0].text)

        self.assertEqual(chunks[1].page_number == 2, True)
        self.assertEqual(chunks[1].header, "Retention Policy")
        self.assertIn("[policy.pdf - p.2] Retention Policy", chunks[1].text)

    def test_chunk_hash_deduplication(self):
        """Verify exact chunk deduplication by source_hash."""
        c1 = Chunk(
            text="[doc - p.1] Header\n\nIdentical content",
            raw_text="Identical content",
            header="Header",
            page_number=1,
            chunk_index=0,
            source_hash=compute_chunk_hash("[doc - p.1] Header\n\nIdentical content"),
            doc_hash="hash1",
            filename="doc.pdf"
        )
        c2 = Chunk(
            text="[doc - p.1] Header\n\nIdentical content",
            raw_text="Identical content",
            header="Header",
            page_number=1,
            chunk_index=1,
            source_hash=compute_chunk_hash("[doc - p.1] Header\n\nIdentical content"),
            doc_hash="hash1",
            filename="doc.pdf"
        )
        c3 = Chunk(
            text="[doc - p.1] Header\n\nUnique content",
            raw_text="Unique content",
            header="Header",
            page_number=1,
            chunk_index=2,
            source_hash=compute_chunk_hash("[doc - p.1] Header\n\nUnique content"),
            doc_hash="hash1",
            filename="doc.pdf"
        )

        unique = deduplicate_chunks([c1, c2, c3])
        self.assertEqual(len(unique), 2)
        self.assertNotEqual(unique[0].source_hash, unique[1].source_hash)


if __name__ == "__main__":
    unittest.main()
