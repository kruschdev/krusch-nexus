import os
import sys
import unittest
from unittest.mock import MagicMock

# Set Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import src.backend.test_stubs

from src.backend.auth import validate_nexus_api_key

class TestLicenseMonetization(unittest.TestCase):

    def test_missing_api_key(self):
        res = validate_nexus_api_key("")
        self.assertFalse(res["valid"])
        self.assertIn("Missing NEXUS_API_KEY", res["error"])

    def test_dev_bypass_key(self):
        res = validate_nexus_api_key("nx_dev_test123")
        self.assertTrue(res["valid"])
        self.assertEqual(res["subscription_tier"], "pro")
        self.assertEqual(res["username"], "dev_subscriber")

    def test_live_subscriber_key(self):
        res = validate_nexus_api_key("nx_live_abc999")
        self.assertTrue(res["valid"])
        self.assertEqual(res["subscription_tier"], "pro")

    def test_invalid_api_key(self):
        res = validate_nexus_api_key("invalid_key_xyz")
        self.assertFalse(res["valid"])
        self.assertIn("Invalid or expired", res["error"])

    def test_database_user_api_key(self):
        mock_db = MagicMock()
        mock_user = MagicMock()
        mock_user.username = "acme_corp"
        mock_user.subscription_tier = "enterprise"
        mock_db.query().filter().first.return_value = mock_user

        res = validate_nexus_api_key("nx_corp_key", db=mock_db)
        self.assertTrue(res["valid"])
        self.assertEqual(res["username"], "acme_corp")
        self.assertEqual(res["subscription_tier"], "enterprise")

if __name__ == '__main__':
    unittest.main()
