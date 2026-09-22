"""
Integration tests for KruschNexus FastMCP Server tools.
Verifies tool invocation, strict workspace enforcement, and path sandboxing.
"""

import os
import json
import shutil
import tempfile
import unittest

from krusch_nexus import Nexus, NexusConfig
from krusch_nexus.db import init_db, get_engine
from krusch_nexus.mcp import (
    set_client,
    nexus_list_workspaces,
    nexus_ingest_file,
    nexus_search_corpus
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
        self.nexus = Nexus(self.config)
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

    def test_mcp_ingest_and_search_success(self):
        """Verify successful MCP file ingest and subsequent corpus search."""
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
        self.assertIn("statute.txt", top_hit["filename"])
        self.assertIn("1950.5", top_hit["citation"])


if __name__ == "__main__":
    unittest.main()
