"""
tests/unit/test_chaos_resumption.py
===================================
Chaos and operational reliability tests:
1. Chaos simulation: Kill during OCR, embed batch, and archive rename with clean resumption.
2. Hardened idempotency key (workspace, file_hash, parser_version, embed_model, embed_dim).
3. Per-workspace disk quota enforcement.
"""

import os
import shutil
import tempfile
import unittest

from krusch_nexus import NexusClient, NexusConfig, DocType
from krusch_nexus.store import init_db, get_engine, Document, DocumentChunk, get_db_session
from krusch_nexus.exceptions import TooLargeError


class TestChaosResumption(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nexus_chaos_")
        self.db_path = os.path.join(self.temp_dir, "test_chaos.db")
        self.config = NexusConfig(
            database_url=f"sqlite:///{self.db_path}",
            allowed_ingest_roots=[self.temp_dir],
            workspace_disk_quota_bytes=50000  # 50 KB quota for testing
        )
        self.engine = get_engine(self.config.database_url)
        init_db(self.engine)
        self.client = NexusClient(self.config)

        self.doc_file = os.path.join(self.temp_dir, "contract_chaos.txt")
        with open(self.doc_file, "w", encoding="utf-8") as f:
            f.write(
                "Section 1.1 Scope of Operations\n"
                "The contractor shall perform robust systems administration tasks.\n\n"
                "Section 1.2 Confidentiality and Security\n"
                "All server configurations and credentials must remain strictly confidential.\n\n"
                "Section 1.3 Termination for Convenience\n"
                "Either party may terminate upon thirty days prior written notice."
            )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_kill_during_ocr_and_resume(self):
        """Assert simulated kill during OCR terminates and second attempt recovers cleanly."""
        from krusch_nexus.ingest import IngestPipeline
        pipeline = IngestPipeline(self.config, engine=self.engine)

        with self.assertRaises(SystemExit):
            pipeline.process_file(
                filepath=self.doc_file,
                workspace_name="ChaosWorkspace",
                simulate_crash_at="during_ocr"
            )

        # Resumption run should succeed completely
        report = pipeline.process_file(
            filepath=self.doc_file,
            workspace_name="ChaosWorkspace"
        )
        self.assertEqual(report.status, "completed")
        self.assertGreater(report.chunks, 0)

        # Verify no orphan duplicate chunks
        with get_db_session(self.engine) as sess:
            doc = sess.query(Document).filter(Document.workspace_id == 1).first()
            self.assertIsNotNone(doc)
            chunk_count = sess.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).count()
            self.assertEqual(chunk_count, report.chunks)

    def test_kill_during_embed_batch_and_resume(self):
        """Assert simulated kill during embed batch terminates and resumption cleanly cleans up uncommitted chunks."""
        from krusch_nexus.ingest import IngestPipeline
        pipeline = IngestPipeline(self.config, engine=self.engine)

        with self.assertRaises(SystemExit):
            pipeline.process_file(
                filepath=self.doc_file,
                workspace_name="ChaosWorkspace",
                simulate_crash_at="during_embed_batch"
            )

        # Resumption run
        report = pipeline.process_file(
            filepath=self.doc_file,
            workspace_name="ChaosWorkspace"
        )
        self.assertEqual(report.status, "completed")

        with get_db_session(self.engine) as sess:
            chunk_count = sess.query(DocumentChunk).filter(DocumentChunk.workspace_id == 1).count()
            self.assertEqual(chunk_count, report.chunks)

    def test_kill_during_archive_rename_and_resume(self):
        """Assert simulated kill during archive rename leaves DB committed and subsequent ingest detects duplicate."""
        from krusch_nexus.ingest import IngestPipeline
        pipeline = IngestPipeline(self.config, engine=self.engine)

        with self.assertRaises(SystemExit):
            pipeline.process_file(
                filepath=self.doc_file,
                workspace_name="ChaosWorkspace",
                simulate_crash_at="during_archive_rename"
            )

        # Document was committed before the archive rename kill
        # Second run should be an idempotent no-op (skipped_duplicate)
        report = pipeline.process_file(
            filepath=self.doc_file,
            workspace_name="ChaosWorkspace"
        )
        self.assertEqual(report.status, "skipped_duplicate")

    def test_hardened_idempotency_key(self):
        """Assert re-ingest is no-op unless parser_version, embed_model, or embed_dim changes."""
        report1 = self.client.ingest(self.doc_file, workspace="IdempotentWorkspace")
        self.assertEqual(report1.status, "completed")

        # Second ingest with identical config: no-op
        report2 = self.client.ingest(self.doc_file, workspace="IdempotentWorkspace")
        self.assertEqual(report2.status, "skipped_duplicate")

        # Simulate parser version upgrade on the client configuration
        with get_db_session(self.engine) as sess:
            doc = sess.query(Document).filter(Document.workspace_id == 1).first()
            doc.parser_version = "text-plain@1.0"  # Older version
            sess.commit()

        # Re-ingest with newer parser version text-plain@2.0 should re-parse and complete
        report3 = self.client.ingest(self.doc_file, workspace="IdempotentWorkspace")
        self.assertEqual(report3.status, "completed")

    def test_workspace_quota_enforcement(self):
        """Assert exceeding workspace_disk_quota_bytes triggers TooLargeError."""
        # Create a config with tiny quota (500 bytes)
        small_cfg = NexusConfig(
            database_url=f"sqlite:///{self.db_path}",
            allowed_ingest_roots=[self.temp_dir],
            workspace_disk_quota_bytes=200
        )
        client = NexusClient(small_cfg)

        large_doc = os.path.join(self.temp_dir, "large_file.txt")
        with open(large_doc, "w", encoding="utf-8") as f:
            f.write("A" * 1000)

        report = client.ingest(large_doc, workspace="TinyWorkspace")
        self.assertEqual(report.status, "failed")
        self.assertIn("quota exceeded", report.error.lower())

    def test_document_lineage_diff(self):
        """Assert get_document_lineage produces v1 -> v2 section header diffs."""
        doc_v1 = os.path.join(self.temp_dir, "terms.txt")
        with open(doc_v1, "w", encoding="utf-8") as f:
            f.write(
                "Section 1.1 Scope\nWe agree to deliver components.\n\n"
                "Section 1.2 Pricing\nTotal cost is $10,000.\n"
            )
        self.client.ingest(doc_v1, workspace="LineageWorkspace")

        # Version 2 with modified and added section
        with open(doc_v1, "w", encoding="utf-8") as f:
            f.write(
                "Section 1.1 Scope\nWe agree to deliver upgraded components.\n\n"
                "Section 1.2 Pricing\nTotal cost is $12,000.\n\n"
                "Section 1.3 Warranty\nThree-year replacement warranty included.\n"
            )
        self.client.ingest(doc_v1, workspace="LineageWorkspace")

        lineage = self.client.get_document_lineage(filename="terms.txt", workspace="LineageWorkspace")
        self.assertEqual(lineage["version_count"], 2)
        v2 = lineage["versions"][1]
        self.assertEqual(v2["version"], 2)
        diff = v2["diff_from_prior"]
        self.assertIsNotNone(diff)
        self.assertIn("Section 1.3 Warranty", diff["added_headers"])
        self.assertEqual(diff["removed_headers"], [])
        self.assertIn("Section 1.1 Scope", diff["retained_headers"])


if __name__ == "__main__":
    unittest.main()
