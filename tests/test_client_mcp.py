"""
Unit Tests for KruschNexus Client SDK & FastMCP Server
======================================================
Tests public SDK contracts, typed Pydantic responses, and FastMCP tool registrations.
"""

import os
import json
import shutil
import tempfile
import unittest

from src.backend.config import NexusConfig
from src.backend.models import IngestReport, SearchHit, WorkspaceInfo, DocumentInfo
from src.backend.client import NexusIngestClient
from src.backend.db import init_db, get_engine
import src.backend.mcp_server as mcp_server


class TestClientAndMCP(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nexus_client_test_")
        self.db_path = os.path.join(self.temp_dir, "test_client.db")
        self.config = NexusConfig(database_url=f"sqlite:///{self.db_path}")
        self.engine = get_engine(self.config.database_url)
        init_db(self.engine)
        self.client = NexusIngestClient(self.config)
        mcp_server.set_client(self.client)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_mcp_tools_registration(self):
        """Verify that FastMCP registers all 6 canonical KruschNexus tools."""
        tools = mcp_server.mcp._tool_manager._tools
        expected_tools = [
            "nexus_list_workspaces",
            "nexus_list_documents",
            "nexus_ingest_file",
            "nexus_ingest_directory",
            "nexus_get_ingest_report",
            "nexus_search_corpus"
        ]
        for tool_name in expected_tools:
            self.assertIn(tool_name, tools, f"Missing MCP tool: {tool_name}")

    def test_client_typed_ingest_and_search_contract(self):
        """Verify public SDK contract returns strictly typed Pydantic models."""
        test_file = os.path.join(self.temp_dir, "statute.txt")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("Section 8.22.030 Notice Requirements\nLandlords must provide written notice at tenancy inception.")

        # 1. Test ingest_file -> IngestReport
        report = self.client.ingest_file(
            filepath=test_file,
            workspace="Matter_Smith",
            doc_type="authority"
        )
        self.assertIsInstance(report, IngestReport)
        self.assertEqual(report.status, "completed")
        self.assertEqual(report.workspace, "Matter_Smith")
        self.assertEqual(report.filename, "statute.txt")
        self.assertGreater(report.total_chunks, 0)

        # 2. Test search -> List[SearchHit]
        hits = self.client.search(
            query="written notice under Section 8.22",
            workspace="Matter_Smith",
            limit=5
        )
        self.assertIsInstance(hits, list)
        self.assertGreater(len(hits), 0)
        top_hit = hits[0]
        self.assertIsInstance(top_hit, SearchHit)
        self.assertEqual(top_hit.workspace, "Matter_Smith")
        self.assertIn("Section 8.22", top_hit.citation)
        self.assertEqual(top_hit.page_number, 1)

        # 3. Test list_workspaces -> List[WorkspaceInfo]
        workspaces = self.client.list_workspaces()
        self.assertIsInstance(workspaces, list)
        self.assertEqual(len(workspaces), 1)
        self.assertIsInstance(workspaces[0], WorkspaceInfo)
        self.assertEqual(workspaces[0].name, "Matter_Smith")
        self.assertEqual(workspaces[0].document_count, 1)

        # 4. Test list_documents -> List[DocumentInfo]
        docs = self.client.list_documents(workspace="Matter_Smith")
        self.assertIsInstance(docs, list)
        self.assertEqual(len(docs), 1)
        self.assertIsInstance(docs[0], DocumentInfo)
        self.assertEqual(docs[0].filename, "statute.txt")

        # 5. Test get_ingest_report -> Optional[IngestReport]
        fetched_report = self.client.get_ingest_report(report.file_hash)
        self.assertIsInstance(fetched_report, IngestReport)
        self.assertEqual(fetched_report.file_hash, report.file_hash)

    def test_mcp_report_not_found(self):
        """Verify nexus_get_ingest_report returns not_found JSON for non-existent document."""
        report_fn = mcp_server.mcp._tool_manager._tools["nexus_get_ingest_report"].fn
        res_str = report_fn(doc_id_or_hash="non_existent_doc_id_999999")
        data = json.loads(res_str)
        self.assertEqual(data["status"], "not_found")


if __name__ == "__main__":
    unittest.main()
