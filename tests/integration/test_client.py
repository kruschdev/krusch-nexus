"""
Integration tests for KruschNexus client SDK (from krusch_nexus import Nexus).
Verifies:
- Standard library usage: nx = Nexus.from_env()
- Ingestion and hybrid search flow returning typed models
- Mandatory workspace isolation checks
"""

import os
import shutil
import tempfile
import unittest

from krusch_nexus import Nexus, NexusConfig, WorkspaceRequiredError, IngestReport, ChunkHit
from krusch_nexus.db import init_db, get_engine


class TestNexusClient(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nexus_client_test_")
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.config = NexusConfig(
            database_url=f"sqlite:///{self.db_path}",
            allowed_ingest_roots=[self.temp_dir]
        )
        self.engine = get_engine(self.config.database_url)
        init_db(self.engine)
        self.nexus = Nexus(self.config)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_client_ingest_and_search_flow(self):
        """Verify full closed-loop ingest and search with typed Pydantic models."""
        test_file = os.path.join(self.temp_dir, "contract_clause.txt")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(
                "COMMERCIAL LEASE\n\n"
                "Section 8.22 Permitted Use of Premises\n"
                "The tenant shall use the property exclusively for general corporate homelab operations.\n"
            )

        # Ingestion
        report: IngestReport = self.nexus.ingest(
            filepath=test_file,
            workspace="Matter_Acme",
            doc_type="authority"
        )
        self.assertEqual(report.status, "completed")
        self.assertEqual(report.workspace, "Matter_Acme")
        self.assertGreater(report.total_chunks, 0)

        # Search
        hits = self.nexus.search(
            query="permitted use Section 8.22",
            workspace="Matter_Acme",
            limit=3
        )
        self.assertGreaterEqual(len(hits), 1)
        hit: ChunkHit = hits[0]
        self.assertEqual(hit.filename, "contract_clause.txt")
        self.assertEqual(hit.page_number, 1)
        self.assertIn("contract_clause.txt p.1 Section 8.22", hit.citation)

        # List workspaces
        workspaces = self.nexus.list_workspaces()
        self.assertEqual(len(workspaces), 1)
        self.assertEqual(workspaces[0].name, "Matter_Acme")
        self.assertEqual(workspaces[0].document_count, 1)

        # List documents
        docs = self.nexus.list_documents(workspace="Matter_Acme")
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].filename, "contract_clause.txt")

    def test_mandatory_workspace_enforcement(self):
        """Verify operations strictly reject missing workspace argument."""
        test_file = os.path.join(self.temp_dir, "test.txt")
        with open(test_file, "w") as f:
            f.write("Some text")

        with self.assertRaises(WorkspaceRequiredError):
            self.nexus.ingest(filepath=test_file, workspace="")

        with self.assertRaises(WorkspaceRequiredError):
            self.nexus.search(query="test", workspace="")


if __name__ == "__main__":
    unittest.main()
