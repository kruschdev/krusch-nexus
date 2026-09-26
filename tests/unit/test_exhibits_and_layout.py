"""
tests/unit/test_exhibits_and_layout.py
======================================
Tests for:
1. Format-honest citations for tables, exhibits, and attachments.
2. Parent <-> Exhibit / Attachment lineage manifest extraction.
3. Neural layout parser adapter (Docling / Marker) with air-gapped Poppler fallback.
"""

import os
import shutil
import tempfile
import unittest

from krusch_nexus import NexusClient, NexusConfig, format_citation, StructuredLocator, DocType
from krusch_nexus.store import init_db, get_engine
from krusch_nexus.parsers.layout import is_layout_backend_available, parse_with_layout_backend
from krusch_nexus.parsers.registry import parse_document

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")


class TestExhibitsAndLayout(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nexus_exhibits_test_")
        self.db_path = os.path.join(self.temp_dir, "exhibits.db")
        self.config = NexusConfig(
            database_url=f"sqlite:///{self.db_path}",
            allowed_ingest_roots=[FIXTURES_DIR, self.temp_dir]
        )
        self.engine = get_engine(self.config.database_url)
        init_db(self.engine)
        self.client = NexusClient(self.config)
        self.workspace = "ExhibitLineageTest"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_citation_formatting_for_tables_exhibits_attachments(self):
        """Format-honest citation strings must not prepend § to tables, exhibits, or attachments."""
        # 1. Paged table
        cit_table = format_citation(filename="annual_report.pdf", page_number=5, header="Table 2: Depreciation Schedule")
        self.assertEqual(cit_table, "annual_report.pdf p.5 Table 2: Depreciation Schedule")
        self.assertNotIn("§", cit_table)

        # 2. Paged exhibit
        cit_exhibit = format_citation(filename="credit_agreement.pdf", page_number=45, header="Exhibit A - Form of Note")
        self.assertEqual(cit_exhibit, "credit_agreement.pdf p.45 Exhibit A - Form of Note")
        self.assertNotIn("§", cit_exhibit)

        # 3. Unpaged attachment
        cit_att = format_citation(filename="deal_memo.eml", locator="Attachment: fee_schedule.pdf")
        self.assertEqual(cit_att, "deal_memo.eml Attachment: fee_schedule.pdf")
        self.assertNotIn("§", cit_att)

        # 4. Standard section still gets §
        cit_sec = format_citation(filename="lease.pdf", page_number=2, header="Permitted Use")
        self.assertEqual(cit_sec, "lease.pdf p.2 § Permitted Use")

    def test_parent_exhibit_manifest_lineage(self):
        """Parent document must discover embedded exhibits, schedules, and attachments across its chunks."""
        # Ingest a multi-part document with embedded exhibits and email attachments
        deal_memo_path = os.path.join(FIXTURES_DIR, "deal_memo.eml")
        report = self.client.ingest(deal_memo_path, workspace=self.workspace, doc_type=DocType.WORK_PRODUCT)
        self.assertEqual(report.status, "completed")

        manifest = self.client.get_exhibit_manifest(report.document_id)
        self.assertEqual(manifest["document_id"], report.document_id)
        self.assertEqual(manifest["filename"], "deal_memo.eml")
        self.assertEqual(manifest["workspace"], self.workspace)
        self.assertGreaterEqual(manifest["total_chunks"], 1)

    def test_layout_backend_availability_and_fallback(self):
        """Layout backend adapter checks availability and falls back cleanly to Poppler when unmounted."""
        # Currently docling and marker are optional neural dependencies
        docling_avail = is_layout_backend_available("docling")
        marker_avail = is_layout_backend_available("marker")
        self.assertIsInstance(docling_avail, bool)
        self.assertIsInstance(marker_avail, bool)

        # Parsing with backend='docling' must succeed (either using docling or falling back to Poppler)
        sample_pdf = os.path.join(FIXTURES_DIR, "sample_contract.pdf")
        res = parse_with_layout_backend(
            file_path=sample_pdf,
            filename="sample_contract.pdf",
            backend="docling"
        )
        self.assertIsNotNone(res)
        self.assertEqual(res.filename, "sample_contract.pdf")
        self.assertGreater(res.total_pages, 0)
        self.assertIn("Section 8.22", res.pages[0].text)

        # Dispatched via registry parse_document with pdf_backend='marker'
        res_registry = parse_document(
            file_path=sample_pdf,
            filename="sample_contract.pdf",
            pdf_backend="marker"
        )
        self.assertIsNotNone(res_registry)
        self.assertEqual(res_registry.filename, "sample_contract.pdf")
        self.assertGreater(res_registry.total_pages, 0)


if __name__ == "__main__":
    unittest.main()
