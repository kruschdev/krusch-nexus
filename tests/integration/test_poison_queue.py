"""
tests/integration/test_poison_queue.py
======================================
Integration tests for poison file quarantine, listing, and operator replay queue.
"""

import os
import json
import shutil
import tempfile
import unittest

from krusch_nexus import NexusClient, NexusConfig, DocType
from krusch_nexus.store import init_db, get_engine


class TestPoisonQueue(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nexus_poison_test_")
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.watch_dir = os.path.join(self.temp_dir, "watch")
        os.makedirs(self.watch_dir, exist_ok=True)

        self.config = NexusConfig(
            database_url=f"sqlite:///{self.db_path}",
            allowed_ingest_roots=[self.temp_dir],
            watch_dir=self.watch_dir
        )
        self.engine = get_engine(self.config.database_url)
        init_db(self.engine)
        self.nexus = NexusClient(self.config)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_poison_quarantine_listing_and_replay(self):
        """Verify failed file is quarantined with error sidecar, listed, and replayable."""
        ws = "PoisonMatter"

        # 1. Create a 0-byte poison file
        bad_file = os.path.join(self.temp_dir, "corrupt_filing.txt")
        with open(bad_file, "wb") as f:
            f.write(b"")  # 0 bytes -> ParseError

        # Ingest with archive=True -> moves to .failed/PoisonMatter/
        rep = self.nexus.ingest(bad_file, workspace=ws, archive=True)
        self.assertEqual(rep.status, "failed")
        self.assertIn("ParseError", rep.error or "")

        # 2. Verify poison file is listed via client
        poison_items = self.nexus.list_poison_files(workspace=ws)
        self.assertGreaterEqual(len(poison_items), 1)
        target_item = next((it for it in poison_items if it["filename"] == "corrupt_filing.txt"), None)
        self.assertIsNotNone(target_item)
        self.assertEqual(target_item["workspace"], ws)
        self.assertEqual(target_item["error_class"], "ParseError")

        # 3. Simulate operator resolving the corrupt file
        quarantined_path = target_item["filepath"]
        self.assertTrue(os.path.exists(quarantined_path))
        with open(quarantined_path, "wb") as f:
            # Overwrite with valid text content
            f.write(b"Section 1.1 Resolution\nThis document has been recovered and verified.")

        # 4. Replay the poison file
        replay_rep = self.nexus.replay_poison_file("corrupt_filing.txt", workspace=ws, doc_type=DocType.GENERAL)
        self.assertEqual(replay_rep.status, "completed")
        self.assertGreater(replay_rep.total_chunks, 0)

        # 5. Verify poison file and sidecar were removed from .failed/
        self.assertFalse(os.path.exists(quarantined_path))
        sidecar_path = os.path.join(os.path.dirname(quarantined_path), "corrupt_filing.txt.error.json")
        self.assertFalse(os.path.exists(sidecar_path))

        # 6. Verify poison list is now empty for this workspace
        remaining = self.nexus.list_poison_files(workspace=ws)
        self.assertEqual(len(remaining), 0)

    def test_poison_cli_list_and_replay(self):
        """Verify CLI nexus poison list and nexus poison replay commands."""
        import subprocess
        import sys
        ws = "PoisonCLI"

        # Create 0-byte file
        bad_file = os.path.join(self.temp_dir, "broken.txt")
        with open(bad_file, "wb") as f:
            f.write(b"")

        self.nexus.ingest(bad_file, workspace=ws, archive=True)

        env = os.environ.copy()
        env["DATABASE_URL"] = self.config.database_url
        env["ALLOWED_INGEST_ROOTS"] = self.temp_dir

        # 1. nexus poison list --json
        res_list = subprocess.run(
            [sys.executable, "-m", "krusch_nexus.cli", "poison", "list", "--workspace", ws, "--json"],
            capture_output=True, text=True, env=env, check=True
        )
        items = json.loads(res_list.stdout)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["filename"], "broken.txt")

        # Fix file
        quarantined = items[0]["filepath"]
        with open(quarantined, "wb") as f:
            f.write(b"Section 2.2 CLI Fix\nDocument repaired successfully.")

        # 2. nexus poison replay broken.txt --workspace PoisonCLI
        res_replay = subprocess.run(
            [sys.executable, "-m", "krusch_nexus.cli", "poison", "replay", "broken.txt", "--workspace", ws],
            capture_output=True, text=True, env=env, check=True
        )
        rep = json.loads(res_replay.stdout)
        self.assertEqual(rep["status"], "completed")


if __name__ == "__main__":
    unittest.main()
