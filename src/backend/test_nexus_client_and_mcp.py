"""
Test KruschNexus Document Ingestion Client & MCP Tools
=====================================================
Validates:
1. NexusIngestClient (ingest_text, ingest_file, get_ingest_report, search_corpus)
2. MCP server tools (nexus_ingest_file, nexus_get_ingest_report, nexus_search_corpus)
3. KruschBiz staging module integration
"""

import os
import json
import unittest
import uuid
from src.backend.client import NexusIngestClient
from src.backend.biz_rag_staging import get_sop_checklist, get_email_draft_context, find_expert_sme
import src.backend.mcp_server as mcp_server


class TestNexusClientAndMCP(unittest.TestCase):

    def setUp(self):
        self.client = NexusIngestClient()
        self.workspace = f"TestClientWS_{uuid.uuid4().hex[:8]}"

    def test_01_client_ingest_text_and_search(self):
        """Verify programmatic text ingestion, page/chunk reporting, and hybrid citation search."""
        doc_content = (
            "### Section 2.1 Confidential Ingestion Protocol\n"
            "All firm documents must remain on local nodes without external API egress.\n"
            "### Section 2.2 Retention Standards\n"
            "Client files are archived into safe storage directories with SHA-256 hashes."
        )
        report = self.client.ingest_text(
            content=doc_content,
            filename="firm_security_policy.md",
            workspace=self.workspace,
            doc_type="authority"
        )
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["pages_in"], 1)
        self.assertGreaterEqual(report["chunks_out"], 1)

        # Retrieve stored report by document ID
        doc_id = report["document_id"]
        stored_report = self.client.get_ingest_report(str(doc_id))
        self.assertIsNotNone(stored_report)
        self.assertEqual(stored_report["filename"], "firm_security_policy.md")

        # Hybrid search over the workspace
        results = self.client.search_corpus("external API egress", workspace=self.workspace)
        self.assertGreater(len(results), 0)
        top = results[0]
        self.assertIn("firm_security_policy.md", top["citation"])
        self.assertIn("Section 2.1", top["header"])

    def test_02_mcp_tools_registration_and_execution(self):
        """Verify new FastMCP tools execute properly and return valid JSON."""
        # Test nexus_search_corpus via MCP tool handler
        search_fn = mcp_server.mcp._tool_manager._tools["nexus_search_corpus"].fn
        res_json = search_fn(query="retention standards", workspace_name=self.workspace)
        data = json.loads(res_json)
        self.assertEqual(data["status"], "success")

        # Test nexus_get_ingest_report via MCP tool handler
        report_fn = mcp_server.mcp._tool_manager._tools["nexus_get_ingest_report"].fn
        res_rep = report_fn(doc_id_or_hash="99999999")
        data_rep = json.loads(res_rep)
        self.assertEqual(data_rep["status"], "not_found")

    def test_03_kruschbiz_staging_helpers(self):
        """Verify business RAG functions staged for KruschBiz work without breaking."""
        email_ctx = get_email_draft_context("security retention guidelines")
        self.assertIn("status", email_ctx)
        self.assertEqual(email_ctx["status"], "success")

        expert_res = find_expert_sme("cybersecurity retention")
        self.assertIn("status", expert_res)
        self.assertEqual(expert_res["status"], "success")


if __name__ == "__main__":
    unittest.main()
