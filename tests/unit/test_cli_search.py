"""Unit tests for cmd_search in krusch_nexus.cli."""

import io
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from krusch_nexus.cli import cmd_search


class TestCliSearch(unittest.TestCase):
    def _make_args(self, **overrides):
        defaults = {
            "query": "indemnification cap",
            "workspace": "acme_corp",
            "doc_type": None,
            "limit": 5,
            "mode": "hybrid",
            "include_superseded": False,
            "include_low_ocr": False,
            "quiet": False,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_cmd_search_success_with_hits(self):
        fake_hit = SimpleNamespace(
            citation="MSA-2026 § 4.2",
            score=0.912,
            header="Limitation of Liability",
            match_reasons=["lexical_bm25", "vector_cosine"],
            text="Liability shall not exceed twelve months of fees.",
        )
        mock_client = MagicMock()
        mock_client.search.return_value = [fake_hit]

        stdout = io.StringIO()
        with patch("krusch_nexus.cli.NexusClient.from_env", return_value=mock_client), \
             patch("sys.stdout", stdout):
            rc = cmd_search(self._make_args())

        self.assertEqual(rc, 0)
        output = stdout.getvalue()
        self.assertIn("Found 1 hit(s) in workspace 'acme_corp'", output)
        self.assertIn("MSA-2026 § 4.2", output)

    def test_cmd_search_empty_hits(self):
        mock_client = MagicMock()
        mock_client.search.return_value = []

        stdout = io.StringIO()
        with patch("krusch_nexus.cli.NexusClient.from_env", return_value=mock_client), \
             patch("sys.stdout", stdout):
            rc = cmd_search(self._make_args())

        self.assertEqual(rc, 0)
        self.assertIn("No matching documents found in workspace 'acme_corp'.", stdout.getvalue())

    def test_cmd_search_handles_exception_gracefully(self):
        mock_client = MagicMock()
        mock_client.search.side_effect = RuntimeError("Database connection refused")

        stderr = io.StringIO()
        with patch("krusch_nexus.cli.NexusClient.from_env", return_value=mock_client), \
             patch("sys.stderr", stderr):
            rc = cmd_search(self._make_args())

        self.assertEqual(rc, 1)
        self.assertIn("Error executing search in workspace 'acme_corp': Database connection refused", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
