import os
import sys
import unittest
from unittest.mock import MagicMock

# Set Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import src.backend.test_stubs

from src.backend.main import get_nexus_agent_setup_json

class TestAgentAccessibility(unittest.TestCase):

    def test_agent_setup_json_spec(self):
        data = get_nexus_agent_setup_json()
        self.assertEqual(data["service"], "Krusch-Nexus Enterprise Business RAG")
        self.assertIn("mcp_sse_endpoint", data)
        self.assertIn("agent_directives", data)
        self.assertIn("nexus_query_business_knowledge", data["mcp_tools"])

if __name__ == '__main__':
    unittest.main()
