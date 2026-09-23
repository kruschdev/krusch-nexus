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
import json
import shutil
import tempfile
import unittest

from krusch_nexus import NexusClient, NexusConfig, DocType
from krusch_nexus.store import init_db, get_engine, get_db_session, DocumentChunk
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


if __name__ == "__main__":
    unittest.main()
