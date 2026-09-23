"""
Integration property tests for Crash-Safe Ingestion Resumption.
Verifies requirement:
- Kill worker after CHUNKED, restart, end in COMMITTED with one document row.
- Crash mid-embed should resume without duplicating chunks.
- Persistent IngestRun state machine tracking in Postgres/SQLite.
"""

import os
import shutil
import tempfile
import unittest

from krusch_nexus.client import NexusClient
from krusch_nexus.models import NexusConfig, IngestState
from krusch_nexus.store import init_db, get_engine, get_db_session, Document, DocumentChunk, IngestRun, Workspace


class TestCrashResumption(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nexus_crash_test_")
        self.db_path = os.path.join(self.temp_dir, "crash_test.db")
        self.config = NexusConfig(
            database_url=f"sqlite:///{self.db_path}",
            allowed_ingest_roots=[self.temp_dir, os.path.abspath("tests/fixtures")],
            embed_batch_size=2
        )
        self.engine = get_engine(self.config.database_url)
        init_db(self.engine)
        self.client = NexusClient(self.config)

        # Create a sample document
        self.sample_doc = os.path.join(self.temp_dir, "crash_contract.txt")
        content = (
            "ARTICLE I: DEFINITIONS\n"
            "The term 'Confidential Information' shall include all non-public proprietary data.\n\n"
            "ARTICLE II: OBLIGATIONS\n"
            "Receiving Party shall preserve confidentiality and refrain from unauthorized disclosures.\n\n"
            "ARTICLE III: REMEDIES\n"
            "Breach of obligations shall entitle Disclosing Party to seek immediate injunctive relief."
        )
        with open(self.sample_doc, "w", encoding="utf-8") as f:
            f.write(content)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_kill_worker_after_chunked_and_resumption(self):
        """
        Property Test: Kill worker process after CHUNKED state.
        On worker restart, the resumed ingestion must end in COMMITTED with
        exactly ONE document row and zero duplicate chunks.
        """
        workspace = "Matter_Crash_Chunked"

        # 1. First run: Simulate crash/SIGKILL immediately after CHUNKED
        with self.assertRaises(SystemExit):
            self.client.ingest(
                filepath=self.sample_doc,
                workspace=workspace,
                archive=False,
                simulate_crash_after_state=IngestState.CHUNKED
            )

        # 2. Verify intermediate database state:
        # IngestRun must show 'chunked', documents must NOT be committed yet
        with get_db_session(self.engine) as db:
            runs = db.query(IngestRun).filter(IngestRun.workspace == workspace).all()
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0].state, IngestState.CHUNKED.value)

            doc_count = db.query(Document).join(Workspace).filter(Workspace.name == workspace).count()
            self.assertEqual(doc_count, 0, "No document should be committed before completion")

        # 3. Simulate worker restart with fresh client/pipeline
        restarted_client = NexusClient(self.config)
        report = restarted_client.ingest(
            filepath=self.sample_doc,
            workspace=workspace,
            archive=False
        )

        self.assertEqual(report.status, "completed")
        self.assertGreater(report.chunks, 0)

        # 4. Assert invariant: Exactly ONE document row in DB, matching chunk count, zero duplicates
        with get_db_session(self.engine) as db:
            docs = db.query(Document).join(Workspace).filter(Workspace.name == workspace).all()
            self.assertEqual(len(docs), 1, "Exactly one document row must exist after resumption")
            doc = docs[0]
            self.assertEqual(doc.status, IngestState.COMMITTED.value)

            chunks = db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).all()
            self.assertEqual(len(chunks), report.chunks, "Chunks count must match report count")
            self.assertEqual(len(chunks), doc.total_chunks)

            # Assert total chunks in workspace equals document chunk count (no orphaned chunks)
            total_workspace_chunks = db.query(DocumentChunk).join(Workspace).filter(Workspace.name == workspace).count()
            self.assertEqual(total_workspace_chunks, len(chunks), "Zero duplicate/orphaned chunks in workspace")

            # Assert final IngestRun is committed
            latest_run = db.query(IngestRun).filter(IngestRun.workspace == workspace).order_by(IngestRun.id.desc()).first()
            self.assertIn(latest_run.state, [IngestState.COMMITTED.value, IngestState.ARCHIVED.value])

    def test_kill_worker_after_embedded_and_resumption(self):
        """
        Property Test: Kill worker process after EMBEDDED state.
        On restart, resumption cleanly commits exactly one document with valid embeddings.
        """
        workspace = "Matter_Crash_Embedded"

        with self.assertRaises(SystemExit):
            self.client.ingest(
                filepath=self.sample_doc,
                workspace=workspace,
                archive=False,
                simulate_crash_after_state=IngestState.EMBEDDED
            )

        with get_db_session(self.engine) as db:
            runs = db.query(IngestRun).filter(IngestRun.workspace == workspace).all()
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0].state, IngestState.EMBEDDED.value)

        # Resumed run
        restarted_client = NexusClient(self.config)
        report = restarted_client.ingest(
            filepath=self.sample_doc,
            workspace=workspace,
            archive=False
        )

        self.assertEqual(report.status, "completed")

        with get_db_session(self.engine) as db:
            docs = db.query(Document).join(Workspace).filter(Workspace.name == workspace).all()
            self.assertEqual(len(docs), 1)
            doc_chunks = db.query(DocumentChunk).filter(DocumentChunk.document_id == docs[0].id).all()
            self.assertEqual(len(doc_chunks), report.chunks)


if __name__ == "__main__":
    unittest.main()
