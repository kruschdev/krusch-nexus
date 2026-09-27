"""
tests/unit/test_span_locator.py
===============================
Unit tests for span-true citation spine:
- Persisting character offsets and bounding boxes on chunks and search hits.
- Verifying locator round-tripping: hit → file → slice(char_start, char_end) highlights exact span.
- Noise suppression for running headers, Bates stamps, and exhibit labels.
"""

import os
import re
import shutil
import tempfile
import unittest

from krusch_nexus import NexusClient, NexusConfig, DocType, parse_and_chunk_file
from krusch_nexus.store import init_db, get_engine
from krusch_nexus.parsers.pdf import parse_pdf, BATES_REGEX, EXHIBIT_STAMP_REGEX, FAX_STAMP_REGEX
from krusch_nexus.chunking import chunk_document_pages

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")


class TestSpanLocator(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nexus_span_test_")
        self.db_path = os.path.join(self.temp_dir, "test_span.db")
        self.config = NexusConfig(
            database_url=f"sqlite:///{self.db_path}",
            allowed_ingest_roots=[self.temp_dir, FIXTURES_DIR]
        )
        self.engine = get_engine(self.config.database_url)
        init_db(self.engine)
        self.client = NexusClient(self.config)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_noise_regexes(self):
        """Verify first-class noise regexes match legal Bates stamps, exhibit markers, and fax lines."""
        self.assertTrue(bool(BATES_REGEX.search("PLAINTIFF_0001234")))
        self.assertTrue(bool(BATES_REGEX.search("DEF-004521")))
        self.assertTrue(bool(BATES_REGEX.search("ACME 000123")))
        self.assertTrue(bool(BATES_REGEX.search("CONFIDENTIAL # 000456")))

        self.assertTrue(bool(EXHIBIT_STAMP_REGEX.search("EXHIBIT B")))
        self.assertTrue(bool(EXHIBIT_STAMP_REGEX.search("DEF EX 104")))
        self.assertTrue(bool(EXHIBIT_STAMP_REGEX.search("FILED BY COURT 09/22/2026")))

        self.assertTrue(bool(FAX_STAMP_REGEX.search("TRANSMISSION OK")))
        self.assertTrue(bool(FAX_STAMP_REGEX.search("09/22/2026 14:30 FAX")))

    def test_roundtrip_char_offsets_text(self):
        """Assert SearchHit char_start:char_end round-trips to extract the winning text from file."""
        doc_path = os.path.join(self.temp_dir, "roundtrip_doc.txt")
        doc_body = (
            "ARTICLE I: INTRODUCTORY PROVISIONS\n\n"
            "This Agreement is executed between Alpha Corp and Beta LLC.\n\n"
            "Section 2.1 Confidential Information Defined\n\n"
            "Confidential Information shall strictly denote all non-public proprietary data.\n\n"
            "Section 2.2 Non-Disclosure Covenants\n\n"
            "Neither party shall disclose proprietary data to third parties without prior written consent."
        )
        with open(doc_path, "w", encoding="utf-8") as f:
            f.write(doc_body)

        report = self.client.ingest(doc_path, workspace="SpanWorkspace", doc_type=DocType.WORK_PRODUCT)
        self.assertEqual(report.status, "completed")

        hits = self.client.search("proprietary data", workspace="SpanWorkspace", limit=3)
        self.assertGreater(len(hits), 0)

        # Pick top hit and verify round-trip character highlighting
        hit = hits[0]
        self.assertIsNotNone(hit.char_start)
        self.assertIsNotNone(hit.char_end)
        self.assertLess(hit.char_start, hit.char_end)

        with open(doc_path, "r", encoding="utf-8") as f:
            full_file_text = f.read()

        highlighted_slice = full_file_text[hit.char_start:hit.char_end]
        # The slice from the original document must match the beginning of the chunk text
        clean_slice = re.sub(r'\s+', ' ', highlighted_slice).strip()
        clean_hit = re.sub(r'\s+', ' ', hit.text).strip()
        self.assertTrue(
            clean_slice in clean_hit or clean_hit in clean_slice,
            f"Highlighted slice '{clean_slice}' not in hit text '{clean_hit}'"
        )

    def test_pdf_bounding_boxes_extraction(self):
        """Assert PDF parser extracts bounding boxes from Poppler TSV into content blocks."""
        pdf_fixture = os.path.join(FIXTURES_DIR, "sample_contract.pdf")
        if not os.path.exists(pdf_fixture):
            self.skipTest("sample_contract.pdf fixture not found")

        res = parse_pdf(pdf_fixture, "sample_contract.pdf")
        self.assertGreater(len(res.pages), 0)
        p1 = res.pages[0]

        # Check blocks have bounding boxes
        blocks_with_bbox = [b for b in p1.blocks if b.bbox is not None]
        self.assertGreater(len(blocks_with_bbox), 0, "PDF pages must have blocks with bounding boxes")

        first_bbox = blocks_with_bbox[0].bbox
        self.assertEqual(len(first_bbox), 4)
        # Bounding box coordinates must be non-negative
        self.assertGreaterEqual(first_bbox[0], 0)
        self.assertGreaterEqual(first_bbox[1], 0)
        self.assertGreater(first_bbox[2], 0)  # width
        self.assertGreater(first_bbox[3], 0)  # height

        # Verify chunking carries bounding boxes
        chunks = chunk_document_pages(res.pages, "sample_contract.pdf", "hash123")
        self.assertGreater(len(chunks), 0)
        self.assertIsNotNone(chunks[0].bbox, "Chunk bounding box must be populated from block union")
        self.assertEqual(len(chunks[0].bbox), 4)

    def test_pdf_sample_contract_span_and_bbox_roundtrip(self):
        """Assert SearchHit on sample_contract.pdf matches the documented contract in docs/why_we_built_krusch_nexus.md."""
        pdf_fixture = os.path.join(FIXTURES_DIR, "sample_contract.pdf")
        if not os.path.exists(pdf_fixture):
            self.skipTest("sample_contract.pdf fixture not found")

        report = self.client.ingest(pdf_fixture, workspace="SpanWorkspace", doc_type=DocType.AUTHORITY)
        self.assertEqual(report.status, "completed")

        hits = self.client.search("commercial office space Section 8.22", workspace="SpanWorkspace", limit=3)
        self.assertGreater(len(hits), 0)

        hit = hits[0]
        # Invariant 1: Physical page is 1
        self.assertEqual(hit.page_number, 1)
        self.assertIn("p.1", hit.citation)

        # Invariant 2: Character offsets exactly slice the extracted page text
        res = parse_pdf(pdf_fixture, "sample_contract.pdf")
        p1_text = res.pages[hit.page_number - 1].text
        self.assertIsNotNone(hit.char_start)
        self.assertIsNotNone(hit.char_end)
        self.assertEqual(p1_text[hit.char_start:hit.char_end], hit.text)

        # Invariant 3: Bounding box matches exact point coordinates (union of title and section header)
        self.assertIsNotNone(hit.bbox)
        self.assertEqual(hit.bbox, [50.0, 83.38, 212.75, 41.1])

    def test_ocr_scan_span_and_bbox_roundtrip(self):
        """Assert OCR scanned page (scanned_page.pdf) preserves bounding boxes and exact slice roundtrip."""
        pdf_fixture = os.path.join(FIXTURES_DIR, "scanned_page.pdf")
        if not os.path.exists(pdf_fixture):
            self.skipTest("scanned_page.pdf fixture not found")

        report = self.client.ingest(pdf_fixture, workspace="SpanWorkspace", doc_type=DocType.AUTHORITY)
        self.assertEqual(report.status, "completed")

        hits = self.client.search("liquidated damages fifty thousand dollars", workspace="SpanWorkspace", limit=3)
        self.assertGreater(len(hits), 0)

        hit = hits[0]
        self.assertEqual(hit.page_number, 1)
        self.assertIn("p.1", hit.citation)

        # Invariant: OCR bounding box is present with 4 non-negative coordinates
        self.assertIsNotNone(hit.bbox)
        self.assertEqual(len(hit.bbox), 4)
        self.assertGreater(hit.bbox[2], 0)  # width
        self.assertGreater(hit.bbox[3], 0)  # height

        # Invariant: Character offsets slice the OCR page text bit-for-bit
        from krusch_nexus.parsers import parse_document
        res = parse_document(pdf_fixture, "scanned_page.pdf")
        p1_text = res.pages[hit.page_number - 1].text
        self.assertIsNotNone(hit.char_start)
        self.assertIsNotNone(hit.char_end)
        self.assertEqual(p1_text[hit.char_start:hit.char_end], hit.text)

    def test_format_honest_citation_exemptions(self):
        """Assert format_citation avoids spurious § prefixes on Item, Schedule, Clause, Appendix, and Paragraph."""
        from krusch_nexus.models import format_citation

        # Exempt prefixes
        self.assertEqual(
            format_citation("10k.pdf", page_number=1, header="Item 8. Consolidated Financial Statements"),
            "10k.pdf p.1 Item 8. Consolidated Financial Statements"
        )
        self.assertEqual(
            format_citation("lease.pdf", page_number=3, header="Schedule B: Permitted Exceptions"),
            "lease.pdf p.3 Schedule B: Permitted Exceptions"
        )
        self.assertEqual(
            format_citation("contract.pdf", page_number=5, header="Clause 14.1 Liquidated Damages"),
            "contract.pdf p.5 Clause 14.1 Liquidated Damages"
        )
        self.assertEqual(
            format_citation("specs.pdf", page_number=2, header="Appendix A Technical Requirements"),
            "specs.pdf p.2 Appendix A Technical Requirements"
        )
        self.assertEqual(
            format_citation("filing.pdf", page_number=4, header="Paragraph 12 Statement of Facts"),
            "filing.pdf p.4 Paragraph 12 Statement of Facts"
        )

        # Non-exempt headings should retain § prefix
        self.assertEqual(
            format_citation("code.txt", page_number=None, header="1950.5 Security Deposits"),
            "code.txt § 1950.5 Security Deposits"
        )
        self.assertEqual(
            format_citation("memo.docx", page_number=None, header="Confidential Information Defined"),
            "memo.docx § Confidential Information Defined"
        )

    def test_parse_file_and_parse_and_chunk_zero_config(self):
        """Assert top-level parse_file and parse_and_chunk_file operate without database or config setup."""
        import krusch_nexus

        pdf_fixture = os.path.join(FIXTURES_DIR, "sample_contract.pdf")
        if not os.path.exists(pdf_fixture):
            self.skipTest("sample_contract.pdf fixture not found")

        # 1. parse_file: Zero-config parsing
        res = krusch_nexus.parse_file(pdf_fixture)
        self.assertIsNotNone(res)
        self.assertEqual(res.filename, "sample_contract.pdf")
        self.assertEqual(len(res.pages), 2)
        self.assertGreater(len(res.pages[0].blocks), 0)
        self.assertIsNotNone(res.pages[0].blocks[0].bbox)

        # 2. parse_and_chunk_file: Zero-config chunking with provenance
        res2, chunks = krusch_nexus.parse_and_chunk_file(pdf_fixture)
        self.assertEqual(len(res2.pages), 2)
        self.assertGreater(len(chunks), 0)
        self.assertIn("citation", chunks[0])
        self.assertIn("p.1", chunks[0]["citation"])
        self.assertIsNotNone(chunks[0]["bbox"])
        self.assertIsNotNone(chunks[0]["source_hash"])

    def test_client_parse_file_library_mode(self):
        """Assert client.parse() and client.parse_file() parse documents without database mutations."""
        pdf_fixture = os.path.join(FIXTURES_DIR, "sample_contract.pdf")
        if not os.path.exists(pdf_fixture):
            self.skipTest("sample_contract.pdf fixture not found")

        res = self.client.parse(pdf_fixture)
        self.assertEqual(res.filename, "sample_contract.pdf")
        self.assertEqual(len(res.pages), 2)

        res2 = self.client.parse_file(pdf_fixture)
        self.assertEqual(res2.filename, "sample_contract.pdf")

    def test_pdf_sec_10k_table_line_bboxes(self):
        """Assert multi-row financial table (heldout_sec_10k_table.pdf) extracts distinct line blocks and bboxes."""
        pdf_fixture = os.path.join(FIXTURES_DIR, "heldout_sec_10k_table.pdf")
        if not os.path.exists(pdf_fixture):
            self.skipTest("heldout_sec_10k_table.pdf fixture not found")

        res = parse_pdf(pdf_fixture, "heldout_sec_10k_table.pdf")
        self.assertEqual(len(res.pages), 1)
        p1 = res.pages[0]

        # Verify that all 12 table and report lines are preserved as distinct blocks with individual bboxes
        self.assertEqual(len(p1.blocks), 12)
        for b in p1.blocks:
            self.assertIsNotNone(b.bbox)
            self.assertEqual(len(b.bbox), 4)
            self.assertEqual(b.bbox[0], 50.0)  # Left margin
            self.assertGreater(b.bbox[1], 0)   # Top coordinate
            self.assertGreater(b.bbox[2], 0)   # Width
            self.assertGreater(b.bbox[3], 0)   # Height

        # Top coordinates must strictly increase down the page
        tops = [b.bbox[1] for b in p1.blocks]
        self.assertEqual(tops, sorted(tops))

        # Check specific financial row bounding boxes
        rev_block = next((b for b in p1.blocks if "Revenue: 2026" in b.text), None)
        self.assertIsNotNone(rev_block)
        self.assertEqual(rev_block.bbox, [50.0, 124.82, 253.72, 9.25])

        rnd_block = next((b for b in p1.blocks if "Research and Development" in b.text), None)
        self.assertIsNotNone(rnd_block)
        self.assertEqual(rnd_block.bbox, [50.0, 178.82, 332.08, 9.25])

    def test_adversarial_fax_stamp_noise_segmentation(self):
        """Assert rubber stamps and fax headers are cleanly segmented into noise blocks without chunk body pollution."""
        fax_fixture = os.path.join(FIXTURES_DIR, "adversarial_fax_stamp.pdf")
        if not os.path.exists(fax_fixture):
            self.skipTest("adversarial_fax_stamp.pdf fixture not found")

        res, chunks = parse_and_chunk_file(fax_fixture)
        self.assertEqual(len(res.pages), 1)
        p1 = res.pages[0]

        # Verify noise blocks are categorized into typed ContentBlock instances
        noise_types = {getattr(b, "block_type", "") for b in p1.blocks}
        self.assertIn("fax_stamp", noise_types)
        self.assertIn("exhibit_stamp", noise_types)
        self.assertIn("header_footer", noise_types)

        # Verify exactly 2 substantive contract chunks are produced (no bogus stamp-only chunks)
        self.assertEqual(len(chunks), 2)
        headers = [c["header"] for c in chunks]
        self.assertIn("Section 12.4 Indemnification and Defense Obligations", headers)
        self.assertIn("Section 125 Limitation of Liability", headers)

        # Verify noise strings are excluded from substantive chunk text
        for ch in chunks:
            self.assertNotIn("FAX TRANSMISSION", ch["text"])
            self.assertNotIn("SeP.222026", ch["text"])
            self.assertNotIn("RECEIVED", ch["text"])

    def test_adversarial_twocolumn_reading_order_and_column_isolation(self):
        """Assert two-column OCR PDF resolves column reading order and separates Column A from Column B."""
        col_fixture = os.path.join(FIXTURES_DIR, "adversarial_twocolumn.pdf")
        if not os.path.exists(col_fixture):
            self.skipTest("adversarial_twocolumn.pdf fixture not found")

        res, chunks = parse_and_chunk_file(col_fixture)
        self.assertEqual(len(res.pages), 1)
        p1 = res.pages[0]

        # Must extract 9 distinct line blocks
        self.assertEqual(len(p1.blocks), 9)

        # Block 0: Top banner spanning across columns
        b0 = p1.blocks[0]
        self.assertIn("COMMERCIAL CODE", b0.text)

        # Blocks 1-4: Column 1 lines (left-aligned at x ~ 19.2 points)
        col1_blocks = p1.blocks[1:5]
        for b in col1_blocks:
            self.assertLess(b.bbox[0], 50.0, "Column 1 blocks must have left < 50 points")

        self.assertIn("COLUMN", col1_blocks[0].text)
        self.assertIn("Section 9", col1_blocks[1].text)
        self.assertIn("Accession", col1_blocks[2].text)
        self.assertIn("Account", col1_blocks[3].text)

        # Blocks 5-8: Column 2 lines (right-aligned at x ~ 172 points)
        col2_blocks = p1.blocks[5:9]
        for b in col2_blocks:
            self.assertGreater(b.bbox[0], 120.0, "Column 2 blocks must have left > 120 points")

        self.assertIn("COMMENTS", col2_blocks[0].text)
        self.assertIn("Comment 1", col2_blocks[1].text)
        self.assertIn("Comment 2", col2_blocks[2].text)
        self.assertIn("Comment3", col2_blocks[3].text)

        # Assert no horizontal interleaving in extracted text: Column 1 text must appear before Column 2 text
        col1_pos = p1.text.find("Accession")
        col2_pos = p1.text.find("Comment 1")
        self.assertGreater(col1_pos, 0)
        self.assertGreater(col2_pos, 0)
        self.assertLess(col1_pos, col2_pos, "Column 1 text must precede Column 2 comments in natural reading order")


if __name__ == "__main__":
    unittest.main()

