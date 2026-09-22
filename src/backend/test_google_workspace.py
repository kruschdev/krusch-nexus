import os
import sys
import json
import unittest
from unittest.mock import MagicMock

# Set Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import src.backend.test_stubs
from unittest.mock import MagicMock, patch

from src.backend.sync_provider import GoogleWorkspaceDirectoryProvider
from src.backend.mcp_server import nexus_sync_google_workspace

class TestGoogleWorkspaceSync(unittest.TestCase):

    def test_fetch_documents_fallback(self):
        provider = GoogleWorkspaceDirectoryProvider()
        docs = provider.fetch_documents()
        self.assertIsInstance(docs, list)
        self.assertGreater(len(docs), 0)
        self.assertIn("filename", docs[0])

    def test_fetch_emails_fallback(self):
        provider = GoogleWorkspaceDirectoryProvider()
        emails = provider.fetch_emails()
        self.assertIsInstance(emails, list)
        self.assertGreater(len(emails), 0)

    @patch("src.backend.sync_service.index_documents")
    def test_mcp_nexus_sync_google_workspace(self, mock_index):
        mock_index.return_value = 5
        res_json = nexus_sync_google_workspace(workspace_name="General", sync_type="all")
        data = json.loads(res_json)
        self.assertEqual(data["status"], "success")
        self.assertIn("synced_documents_count", data)
        self.assertIn("synced_emails_count", data)

if __name__ == '__main__':
    unittest.main()
