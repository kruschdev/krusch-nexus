"""
Integration tests for KruschNexus FastMCP Server tools.
Verifies tool invocation, strict workspace enforcement, structured citations, and operator action guards.
"""

import os
import json
import shutil
import tempfile
import unittest

from krusch_nexus import NexusClient, NexusConfig
from krusch_nexus.store import init_db, get_engine
from krusch_nexus.mcp import (
    set_client,
    nexus_list_workspaces,
    nexus_ingest_file,
    nexus_search_corpus,
    nexus_delete_document,
    nexus_reparse
)


class TestMCPIntegration(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nexus_mcp_test_")
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.config = NexusConfig(
            database_url=f"sqlite:///{self.db_path}",
            allowed_ingest_roots=[self.temp_dir]
        )
        self.engine = get_engine(self.config.database_url)
        init_db(self.engine)
        self.nexus = NexusClient(self.config)
        set_client(self.nexus)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_mcp_ingest_requires_workspace(self):
        """Verify MCP ingest tool refuses execution if workspace_name is empty."""
        test_file = os.path.join(self.temp_dir, "legal_doc.txt")
        with open(test_file, "w") as f:
            f.write("Some legal content")

        resp_raw = nexus_ingest_file(file_path=test_file, workspace_name="")
        resp = json.loads(resp_raw)
        self.assertEqual(resp["status"], "error")
        self.assertIn("workspace_name is required", resp["error"])

    def test_mcp_ingest_and_search_success_with_structured_citations(self):
        """Verify successful MCP file ingest and structured citations in search results."""
        test_file = os.path.join(self.temp_dir, "statute.txt")
        with open(test_file, "w") as f:
            f.write("§ 1950.5 Security Deposit Limits\nA landlord may not exceed one month rent.\n")

        ingest_raw = nexus_ingest_file(
            file_path=test_file,
            workspace_name="Matter_MCP_Test",
            doc_type="authority"
        )
        ingest_res = json.loads(ingest_raw)
        self.assertEqual(ingest_res["status"], "completed")

        # Search
        search_raw = nexus_search_corpus(
            query="§ 1950.5 security deposit",
            workspace_name="Matter_MCP_Test"
        )
        search_res = json.loads(search_raw)
        self.assertEqual(search_res["status"], "success")
        self.assertGreater(search_res["results_count"], 0)
        top_hit = search_res["results"][0]

        # Verify structured citation dictionary
        citation = top_hit["citation"]
        self.assertEqual(citation["filename"], "statute.txt")
        self.assertIn("1950.5", citation["formatted"])
        self.assertIn("page_number", citation)
        self.assertIn("locator", citation)
        self.assertIn("header", citation)

    def test_mcp_operator_guard_on_delete_and_reparse(self):
        """Verify operator-only tools reject execution without explicit operator_confirmed=True."""
        # delete_document without confirmation fails
        del_unconfirmed = json.loads(nexus_delete_document(document_id=1, operator_confirmed=False))
        self.assertEqual(del_unconfirmed["status"], "error")
        self.assertIn("operator action", del_unconfirmed["error"])

        # reparse without confirmation fails
        reparse_unconfirmed = json.loads(nexus_reparse(document_id=1, operator_confirmed=False))
        self.assertEqual(reparse_unconfirmed["status"], "error")
        self.assertIn("operator", reparse_unconfirmed["error"])

    def test_mcp_search_with_sql_filters(self):
        """Verify MCP search passes SQL filters (filename, page, doc_id)."""
        test_file = os.path.join(self.temp_dir, "statute_filtered.txt")
        with open(test_file, "w") as f:
            f.write("Section 500 Filtered Clause Content\n")

        nexus_ingest_file(file_path=test_file, workspace_name="Matter_Filter_Test")

        # Search matching filename
        match_raw = nexus_search_corpus(
            query="Clause",
            workspace_name="Matter_Filter_Test",
            filename="statute_filtered.txt"
        )
        match_res = json.loads(match_raw)
        self.assertEqual(match_res["results_count"], 1)

        # Search non-matching filename
        no_match_raw = nexus_search_corpus(
            query="Clause",
            workspace_name="Matter_Filter_Test",
            filename="other_file.txt"
        )
        no_match_res = json.loads(no_match_raw)
        self.assertEqual(no_match_res["results_count"], 0)

    def test_mcp_token_workspace_acl(self):
        """Verify token ACL restricts workspace listing, ingestion, and retrieval."""
        self.config.token_workspaces = {
            "tok_alpha": ["Matter_Alpha"],
            "tok_beta": ["Matter_Beta"]
        }

        f_alpha = os.path.join(self.temp_dir, "alpha.txt")
        with open(f_alpha, "w") as f:
            f.write("Alpha confidential legal memorandum.\n")

        # 1. Ingest without token -> fails
        unauth_raw = nexus_ingest_file(file_path=f_alpha, workspace_name="Matter_Alpha", token=None)
        unauth_res = json.loads(unauth_raw)
        self.assertEqual(unauth_res["status"], "error")
        self.assertIn("Authentication required", unauth_res["error"])

        # 2. Ingest with wrong token -> fails
        wrong_raw = nexus_ingest_file(file_path=f_alpha, workspace_name="Matter_Alpha", token="tok_beta")
        wrong_res = json.loads(wrong_raw)
        self.assertEqual(wrong_res["status"], "error")
        self.assertIn("not authorized", wrong_res["error"])

        # 3. Ingest with correct token -> succeeds
        ok_raw = nexus_ingest_file(file_path=f_alpha, workspace_name="Matter_Alpha", token="tok_alpha")
        ok_res = json.loads(ok_raw)
        self.assertEqual(ok_res["status"], "completed")

        # 4. List workspaces with tok_alpha -> only Matter_Alpha is listed
        list_raw = nexus_list_workspaces(token="tok_alpha")
        list_res = json.loads(list_raw)
        ws_names = [w["name"] for w in list_res["workspaces"]]
        self.assertIn("Matter_Alpha", ws_names)
        self.assertNotIn("Matter_Filter_Test", ws_names)

        # 5. Search Matter_Alpha with tok_alpha -> succeeds
        search_ok_raw = nexus_search_corpus(query="confidential", workspace_name="Matter_Alpha", token="tok_alpha")
        search_ok_res = json.loads(search_ok_raw)
        self.assertEqual(search_ok_res["status"], "success")

        # 6. Search Matter_Alpha with tok_beta -> fails
        search_denied_raw = nexus_search_corpus(query="confidential", workspace_name="Matter_Alpha", token="tok_beta")
        search_denied_res = json.loads(search_denied_raw)
        self.assertEqual(search_denied_res["status"], "error")
        self.assertIn("not authorized", search_denied_res["error"])


if __name__ == "__main__":
    unittest.main()
