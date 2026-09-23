"""
Integration Tests for KruschNexus REST API (HTTP Twin).
Tests:
- /health service status
- /v1/workspaces CRUD and conflict handling
- /v1/ingest multipart upload with size capping (413 Payload Too Large)
- /v1/search hybrid retrieval over HTTP
- /v1/documents list, fetch report, and operator token protection for delete
"""

import os
import shutil
import tempfile
import unittest
from fastapi.testclient import TestClient

from krusch_nexus.models import NexusConfig
from krusch_nexus.store import init_db, get_engine
import krusch_nexus.api as api_module
from krusch_nexus.client import NexusClient


class TestApiEndpoints(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nexus_api_test_")
        self.db_path = os.path.join(self.temp_dir, "test_api.db")
        self.config = NexusConfig(
            database_url=f"sqlite:///{self.db_path}",
            allowed_ingest_roots=[self.temp_dir],
            operator_token="secret_operator_token_xyz"
        )
        self.engine = get_engine(self.config.database_url)
        init_db(self.engine)

        api_module.config = self.config
        api_module.client = NexusClient(self.config)
        self.client = TestClient(api_module.app)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_healthcheck_endpoint(self):
        """Verify /health returns structured service health status."""
        resp = self.client.get("/health")
        self.assertIn(resp.status_code, [200, 503])
        data = resp.json()
        self.assertIn("database", data)
        self.assertIn("ollama", data)
        self.assertIn("embed_model", data)

    def test_workspaces_crud(self):
        """Verify creating and listing workspaces via HTTP."""
        resp = self.client.post("/v1/workspaces", json={"name": "Litigation", "description": "Active lawsuits"})
        self.assertEqual(resp.status_code, 200)
        ws_data = resp.json()
        self.assertEqual(ws_data["name"], "Litigation")
        self.assertEqual(ws_data["document_count"], 0)

        # Duplicate should return 409 Conflict
        dup_resp = self.client.post("/v1/workspaces", json={"name": "Litigation"})
        self.assertEqual(dup_resp.status_code, 409)

        # List workspaces
        list_resp = self.client.get("/v1/workspaces")
        self.assertEqual(list_resp.status_code, 200)
        workspaces = list_resp.json()
        self.assertEqual(len(workspaces), 1)
        self.assertEqual(workspaces[0]["name"], "Litigation")

    def test_ingest_and_search_endpoints(self):
        """Verify multipart document ingestion and hybrid search over HTTP."""
        file_content = b"Section 4.1 Indemnification\nEach party agrees to indemnify and hold harmless."
        files = {"file": ("indemnity.txt", file_content, "text/plain")}
        data = {"workspace": "Contracts", "doc_type": "authority"}

        ingest_resp = self.client.post("/v1/ingest", files=files, data=data)
        self.assertEqual(ingest_resp.status_code, 200)
        report = ingest_resp.json()
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["workspace"], "Contracts")
        self.assertEqual(report["filename"], "indemnity.txt")
        self.assertGreater(report["total_chunks"], 0)

        # Search corpus
        search_payload = {
            "query": "indemnify and hold harmless under Section 4.1",
            "workspace": "Contracts",
            "limit": 5
        }
        search_resp = self.client.post("/v1/search", json=search_payload)
        self.assertEqual(search_resp.status_code, 200)
        hits = search_resp.json()
        self.assertGreater(len(hits), 0)
        top_hit = hits[0]
        self.assertEqual(top_hit["workspace"], "Contracts")
        self.assertEqual(top_hit["filename"], "indemnity.txt")
        self.assertIn("Section 4.1", top_hit["citation"])

        # List documents
        docs_resp = self.client.get("/v1/documents?workspace=Contracts")
        self.assertEqual(docs_resp.status_code, 200)
        docs = docs_resp.json()
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["filename"], "indemnity.txt")

        # Fetch IngestReport by file hash
        file_hash = report["file_hash"]
        rep_resp = self.client.get(f"/v1/documents/{file_hash}/report")
        self.assertEqual(rep_resp.status_code, 200)
        self.assertEqual(rep_resp.json()["file_hash"], file_hash)

    def test_upload_size_limit_rejection(self):
        """Verify multipart upload exceeding size cap returns HTTP 413."""
        # 51MB payload exceeds 50MB limit
        large_content = b"X" * (51 * 1024 * 1024)
        files = {"file": ("oversized.txt", large_content, "text/plain")}
        data = {"workspace": "Contracts", "doc_type": "general"}

        resp = self.client.post("/v1/ingest", files=files, data=data)
        self.assertEqual(resp.status_code, 413)

    def test_operator_token_protection(self):
        """Verify delete_document endpoint requires valid operator authorization."""
        # Setup doc
        file_content = b"To be deleted under privileged operator oversight."
        files = {"file": ("temporary.txt", file_content, "text/plain")}
        data = {"workspace": "Contracts", "doc_type": "general"}
        ingest_resp = self.client.post("/v1/ingest", files=files, data=data)
        doc_id = ingest_resp.json()["document_id"]

        # Call delete without operator token -> 401 or 403
        del_unauthorized = self.client.delete(f"/v1/documents/{doc_id}")
        self.assertIn(del_unauthorized.status_code, [401, 403])

        # Call delete with invalid token -> 401 or 403
        del_bad = self.client.delete(
            f"/v1/documents/{doc_id}",
            headers={"Authorization": "Bearer bad_token"}
        )
        self.assertIn(del_bad.status_code, [401, 403])

        # Call delete with valid operator token -> 200
        del_ok = self.client.delete(
            f"/v1/documents/{doc_id}",
            headers={"Authorization": "Bearer secret_operator_token_xyz"}
        )
        self.assertEqual(del_ok.status_code, 200)

    def test_ingest_idempotency_semantics(self):
        """Verify explicit on_duplicate HTTP semantics: 200 with skipped_duplicate vs 409 Conflict."""
        file_content = b"Section 9.9 Confidentiality\nAll trade secrets must be protected indefinitely."
        files = {"file": ("nda_idempotent.txt", file_content, "text/plain")}
        data = {"workspace": "IPMatters", "doc_type": "general"}

        # 1. First ingest -> completed (200)
        resp1 = self.client.post("/v1/ingest", files=files, data=data)
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp1.json()["status"], "completed")

        # 2. Second ingest with default / on_duplicate="skip" -> skipped_duplicate (200)
        resp2 = self.client.post("/v1/ingest", files=files, data={**data, "on_duplicate": "skip"})
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json()["status"], "skipped_duplicate")

        # 3. Third ingest with on_duplicate="conflict" -> 409 Conflict
        resp3 = self.client.post("/v1/ingest", files=files, data={**data, "on_duplicate": "conflict"})
        self.assertEqual(resp3.status_code, 409)
        self.assertIn("already exists", resp3.json()["detail"])


if __name__ == "__main__":
    unittest.main()
