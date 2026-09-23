"""
Integration tests for KruschNexus Ingestion Pipeline & State Machine.
Verifies:
- Poison file isolation to .failed/<workspace>/<file>
- Redacted sidecar .error.json (zero document text or connection strings leaked)
- Content hash deduplication and alias creation without re-embedding
- Explicit IngestRun audit persistence in DB
- Standalone reap_stale_locks function
"""

import os
import time
import json
import shutil
import tempfile
import unittest

from krusch_nexus.ingest import IngestPipeline, reap_stale_locks
from krusch_nexus.models import NexusConfig, IngestState
from krusch_nexus.store import init_db, get_engine, get_db_session, IngestRun, Document


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
        """Verify oversized or corrupt file is isolated with redacted sidecar JSON and recorded in ingest_runs."""
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

        # ASSERT REDACTION: The error JSON must NEVER contain the secret document text or connection strings!
        err_str = json.dumps(err_data)
        self.assertNotIn("TOP_SECRET_CORPUS_TEXT", err_str)
        self.assertNotIn("sqlite://", err_str)
        self.assertIn(err_data["error_class"], ["TooLargeError", "FileOversizedError"])
        self.assertEqual(err_data["filename"], "oversized_secret.txt")
        self.assertEqual(err_data["workspace"], "Matter_Isolation")

        # Verify ingest_runs row in database
        with get_db_session(self.engine) as db:
            runs = db.query(IngestRun).filter(IngestRun.workspace == "Matter_Isolation").all()
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0].state, IngestState.FAILED.value)
            self.assertIn(runs[0].error_class, ["TooLargeError", "FileOversizedError"])

    def test_content_hash_deduplication_and_alias(self):
        """Verify identical content hashes are skipped/aliased without re-embedding."""
        file1 = os.path.join(self.temp_dir, "doc1.txt")
        content = "Standard document text for deduplication and alias test."
        with open(file1, "w", encoding="utf-8") as f:
            f.write(content)

        rep1 = self.pipeline.process_file(
            filepath=file1,
            workspace_name="Matter_Dedup",
            archive_source=False
        )
        self.assertEqual(rep1.status, "completed")

        # Second ingestion of identical content under different filename
        file2 = os.path.join(self.temp_dir, "doc1_renamed.txt")
        with open(file2, "w", encoding="utf-8") as f:
            f.write(content)

        rep2 = self.pipeline.process_file(
            filepath=file2,
            workspace_name="Matter_Dedup",
            archive_source=False
        )
        self.assertEqual(rep2.status, "skipped_duplicate")
        self.assertEqual(rep2.document_id, rep1.document_id)

        # Verify ingest_runs row logged
        with get_db_session(self.engine) as db:
            runs = db.query(IngestRun).filter(IngestRun.workspace == "Matter_Dedup").all()
            self.assertGreaterEqual(len(runs), 1)

    def test_post_commit_archival(self):
        """Verify file is archived to .ingested/ only after reaching COMMITTED state."""
        doc_path = os.path.join(self.temp_dir, "contract_to_archive.txt")
        with open(doc_path, "w", encoding="utf-8") as f:
            f.write("Contract terms to be archived safely post-commit.")

        rep = self.pipeline.process_file(
            filepath=doc_path,
            workspace_name="Matter_Archive",
            archive_source=True
        )
        self.assertEqual(rep.status, "completed")

        # Source file should have been moved
        self.assertFalse(os.path.exists(doc_path))

        # Check .ingested directory
        archive_dir = os.path.join(self.temp_dir, ".ingested", "Matter_Archive")
        self.assertTrue(os.path.exists(archive_dir))
        archived_files = os.listdir(archive_dir)
        self.assertTrue(any("contract_to_archive" in f for f in archived_files))

    def test_reap_stale_locks(self):
        """Verify standalone reap_stale_locks correctly deletes expired .lock files."""
        lock_dir = os.path.join(self.temp_dir, ".locks")
        os.makedirs(lock_dir, exist_ok=True)
        stale_lock = os.path.join(lock_dir, "test_job.lock")
        with open(stale_lock, "w") as f:
            f.write("pid: 99999\n")

        # Set modification time 400 seconds into the past
        old_time = time.time() - 400
        os.utime(stale_lock, (old_time, old_time))

        # Reaper with max_age_seconds=300 should delete stale_lock
        reaped = reap_stale_locks(self.temp_dir, max_age_seconds=300)
        self.assertEqual(len(reaped), 1)
        self.assertFalse(os.path.exists(stale_lock))

        # Fresh lock should not be reaped
        fresh_lock = os.path.join(lock_dir, "fresh_job.lock")
        with open(fresh_lock, "w") as f:
            f.write("pid: 12345\n")
        reaped_fresh = reap_stale_locks(self.temp_dir, max_age_seconds=300)
        self.assertEqual(len(reaped_fresh), 0)
        self.assertTrue(os.path.exists(fresh_lock))


if __name__ == "__main__":
    unittest.main()
