"""
Unit Tests for KruschNexus Client and FastMCP Server
====================================================
Tests programmatic client abstractions and FastMCP tool registrations.
Compatible with both standard unittest and pytest.
"""

import json
import unittest
from src.backend.client import NexusIngestClient
import src.backend.mcp_server as mcp_server


class TestClientAndMCP(unittest.TestCase):

    def test_mcp_tools_registration(self):
        """Verify that FastMCP registers all 8 canonical KruschNexus tools."""
        tools = mcp_server.mcp._tool_manager._tools
        expected_tools = [
            "nexus_list_workspaces",
            "nexus_list_documents",
            "nexus_ingest_file",
            "nexus_ingest_directory",
            "nexus_get_ingest_report",
            "nexus_search_corpus",
            "nexus_classify_document",
            "nexus_flag_document_for_review"
        ]
        for tool_name in expected_tools:
            self.assertIn(tool_name, tools, f"Missing MCP tool: {tool_name}")

    def test_mcp_report_not_found(self):
        """Verify nexus_get_ingest_report returns not_found JSON for non-existent document."""
        report_fn = mcp_server.mcp._tool_manager._tools["nexus_get_ingest_report"].fn
        res_str = report_fn(doc_id_or_hash="non_existent_doc_id_999999")
        data = json.loads(res_str)
        self.assertEqual(data["status"], "not_found")

    def test_client_initialization(self):
        """Verify NexusIngestClient initializes without errors."""
        client = NexusIngestClient()
        self.assertIsNotNone(client)

    def test_mcp_nonexistent_file_ingest(self):
        """Verify nexus_ingest_file gracefully handles missing files."""
        ingest_fn = mcp_server.mcp._tool_manager._tools["nexus_ingest_file"].fn
        res_str = ingest_fn(file_path="/tmp/non_existent_file_xyz_123.pdf")
        data = json.loads(res_str)
        self.assertEqual(data.get("status"), "error")


if __name__ == "__main__":
    unittest.main()
