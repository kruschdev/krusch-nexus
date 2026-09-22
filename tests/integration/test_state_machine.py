"""
Integration tests for KruschNexus Ingestion Pipeline & State Machine.
Verifies:
- Poison file isolation to .failed/<workspace>/<file>
- Redacted sidecar .error.json (zero document text leaked)
- Deduplication of identical file hash
"""

import os
import json
import shutil
import tempfile
import unittest

from krusch_nexus.ingest import IngestPipeline
from krusch_nexus.config import NexusConfig
from krusch_nexus.db import init_db, get_engine


class TestStateMachine(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nexus_sm_test_")
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.config = NexusConfig(
            database_url=f"sqlite:///{self.db_path}",
            allowed_ingest_roots=[self.temp_dir],
            max_file_size_bytes=1000  # Cap low for testing
        )
        self.engine = get_engine(self.config.database_url)
        init_db(self.engine)
        self.pipeline = IngestPipeline(self.config)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_oversized_poison_file_isolation_and_redaction(self):
        """Verify oversized or corrupt file is isolated with redacted sidecar JSON."""
        # Create an oversized file
        large_file = os.path.join(self.temp_dir, "oversized_secret.txt")
        secret_content = "TOP_SECRET_CORPUS_TEXT " * 100
        with open(large_file, "w", encoding="utf-8") as f:
            f.write(secret_content)

        report = self.pipeline.process_file(
            filepath=large_file,
            workspace_name="Matter_Isolation",
            archive_source=True
        )

        self.assertEqual(report.status, "failed")
        self.assertIn("exceeds limit", report.error)

        # Check that file was moved to .failed/Matter_Isolation/
        failed_dir = os.path.join(self.temp_dir, ".failed", "Matter_Isolation")
        self.assertTrue(os.path.exists(failed_dir), ".failed directory must exist")

        isolated_file = os.path.join(failed_dir, "oversized_secret.txt")
        self.assertTrue(os.path.exists(isolated_file), "Failed file must be isolated")

        sidecar_file = os.path.join(failed_dir, "oversized_secret.txt.error.json")
        self.assertTrue(os.path.exists(sidecar_file), "Sidecar error JSON must exist")

        with open(sidecar_file, "r", encoding="utf-8") as f:
            err_data = json.load(f)

        # ASSERT REDACTION: The error JSON must NEVER contain the secret document text!
        self.assertNotIn("TOP_SECRET_CORPUS_TEXT", json.dumps(err_data))
        self.assertEqual(err_data["error_class"], "FileOversizedError")
        self.assertEqual(err_data["filename"], "oversized_secret.txt")
        self.assertEqual(err_data["workspace"], "Matter_Isolation")

    def test_content_hash_deduplication(self):
        """Verify identical content hashes are skipped without re-embedding."""
        normal_file = os.path.join(self.temp_dir, "doc1.txt")
        with open(normal_file, "w", encoding="utf-8") as f:
            f.write("Standard document text for deduplication test.")

        rep1 = self.pipeline.process_file(
            filepath=normal_file,
            workspace_name="Matter_Dedup",
            archive_source=False
        )
        self.assertEqual(rep1.status, "completed")

        # Second ingestion of identical content
        rep2 = self.pipeline.process_file(
            filepath=normal_file,
            workspace_name="Matter_Dedup",
            archive_source=False
        )
        self.assertEqual(rep2.status, "skipped_duplicate")
        self.assertEqual(rep2.document_id, rep1.document_id)


if __name__ == "__main__":
    unittest.main()
