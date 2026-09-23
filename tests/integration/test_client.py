"""
Integration tests for KruschNexus client SDK (from krusch_nexus import NexusClient).
Verifies:
- Standard library usage: nx = NexusClient.from_env()
- Ingestion and hybrid search flow returning typed Pydantic models (IngestReport, SearchHit)
- Mandatory workspace isolation checks
- Operator authorization enforcement on delete_document and reparse
"""

import os
import shutil
import tempfile
import unittest

from krusch_nexus import (
    NexusClient,
    NexusConfig,
    WorkspaceRequiredError,
    IngestReport,
    SearchHit,
    DocType,
    AuthenticationError
)
from krusch_nexus.store import init_db, get_engine


class TestNexusClient(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nexus_client_test_")
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.config = NexusConfig(
            database_url=f"sqlite:///{self.db_path}",
            allowed_ingest_roots=[self.temp_dir],
            operator_token="secret_op_xyz"
        )
        self.engine = get_engine(self.config.database_url)
        init_db(self.engine)
        self.nexus = NexusClient(self.config)

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
            doc_type=DocType.AUTHORITY
        )
        self.assertEqual(report.status, "completed")
        self.assertEqual(report.workspace, "Matter_Acme")
        self.assertGreater(report.chunks, 0)

        # Search
        hits = self.nexus.search(
            query="permitted use Section 8.22",
            workspace="Matter_Acme",
            limit=3
        )
        self.assertGreaterEqual(len(hits), 1)
        hit: SearchHit = hits[0]
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

    def test_operator_token_authorization(self):
        """Verify operator-gated actions require operator_token."""
        test_file = os.path.join(self.temp_dir, "privileged.txt")
        with open(test_file, "w") as f:
            f.write("Privileged document text")

        report = self.nexus.ingest(
            filepath=test_file,
            workspace="Matter_Privileged",
            doc_type=DocType.WORK_PRODUCT
        )
        doc_id = report.document_id

        # Calling delete without token fails
        with self.assertRaises(AuthenticationError):
            self.nexus.delete_document(doc_id, operator_token=None)

        # Calling delete with invalid token fails
        with self.assertRaises(AuthenticationError):
            self.nexus.delete_document(doc_id, operator_token="bad_token")

        # Calling delete with valid token succeeds
        success = self.nexus.delete_document(doc_id, operator_token="secret_op_xyz")
        self.assertTrue(success)

    def test_reparse_and_reindex_flow(self):
        """Verify reparse and reindex re-process existing document and recreate chunks."""
        test_file = os.path.join(self.temp_dir, "reindex_sample.txt")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(
                "SECTION 10.1: INDEMNIFICATION\n"
                "Each party shall defend, indemnify, and hold harmless the other party.\n"
            )

        report = self.nexus.ingest(
            filepath=test_file,
            workspace="Matter_Reindex",
            doc_type=DocType.AUTHORITY
        )
        doc_id = report.document_id

        # Calling reparse with valid token
        reparse_report = self.nexus.reparse(doc_id, operator_token="secret_op_xyz")
        self.assertEqual(reparse_report.status, "completed")
        self.assertGreater(reparse_report.chunks, 0)

        # Calling reindex alias
        reindex_report = self.nexus.reindex(reparse_report.document_id, operator_token="secret_op_xyz")
        self.assertEqual(reindex_report.status, "completed")

        # Verify search returns valid hit after re-indexing
        hits = self.nexus.search("indemnification Section 10.1", workspace="Matter_Reindex", limit=1)
        self.assertEqual(len(hits), 1)
        self.assertIn("SECTION 10.1", hits[0].citation)


if __name__ == "__main__":
    unittest.main()
