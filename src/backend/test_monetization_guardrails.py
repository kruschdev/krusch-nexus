import os
import sys
import json
import unittest
from unittest.mock import MagicMock, patch

# Set Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import src.backend.test_stubs

# Dynamic mocks for host testing
sys.modules['llama_index'] = MagicMock()
sys.modules['llama_index.core'] = MagicMock()
sys.modules['sentence_transformers'] = MagicMock()
_orig_repo_ingest = sys.modules.get('src.backend.repo_ingest')
repo_ingest_mock = MagicMock()
repo_ingest_mock.generate_file_tags = MagicMock(return_value={"summary": "SOP refund rule", "tags": ["sop"]})
sys.modules['src.backend.repo_ingest'] = repo_ingest_mock

from src.backend.db import Workspace, Document, User
from src.backend.mcp_server import check_and_increment_query_quota, nexus_query_business_knowledge

class TestMonetizationGuardrails(unittest.TestCase):

    @classmethod
    def tearDownClass(cls):
        if _orig_repo_ingest is not None:
            sys.modules['src.backend.repo_ingest'] = _orig_repo_ingest
        else:
            sys.modules.pop('src.backend.repo_ingest', None)

    def test_workspace_quota_defaults(self):
        ws = Workspace(name="FreeCompany")
        self.assertEqual(ws.subscription_tier, "free")
        self.assertEqual(ws.doc_limit, 5000)
        self.assertEqual(ws.query_limit_monthly, 50000)
        self.assertEqual(ws.queries_used, 0)

    def test_query_quota_increment(self):
        mock_db = MagicMock()
        mock_ws = MagicMock()
        mock_ws.queries_used = 10
        mock_ws.query_limit_monthly = 100
        mock_ws.subscription_tier = "free"
        mock_db.query().filter().first.return_value = mock_ws

        check_and_increment_query_quota(mock_db, workspace_id=1)
        self.assertEqual(mock_ws.queries_used, 11)
        mock_db.commit.assert_called_once()

    def test_query_quota_exceeded(self):
        mock_db = MagicMock()
        mock_ws = MagicMock()
        mock_ws.queries_used = 100
        mock_ws.query_limit_monthly = 100
        mock_ws.subscription_tier = "free"
        mock_db.query().filter().first.return_value = mock_ws

        with self.assertRaises(ValueError) as ctx:
            check_and_increment_query_quota(mock_db, workspace_id=1)
        self.assertIn("Monthly query limit (100) reached", str(ctx.exception))

if __name__ == '__main__':
    unittest.main()
