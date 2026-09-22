import os
import sys
import unittest
from unittest.mock import MagicMock

# Set Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import src.backend.test_stubs

from src.backend.main import get_nexus_landing, get_nexus_docs

class TestLandingSignup(unittest.TestCase):

    def test_landing_page_route(self):
        resp = get_nexus_landing()
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Enterprise Business RAG", resp.body.decode('utf-8'))

    def test_nexus_docs_route(self):
        resp = get_nexus_docs()
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Model Context Protocol (MCP) Setup Guide", resp.body.decode('utf-8'))

if __name__ == '__main__':
    unittest.main()
