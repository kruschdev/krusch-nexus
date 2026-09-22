import os
import sys
import unittest
from unittest.mock import MagicMock

# Set Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import src.backend.test_stubs

from src.backend.main import signup_user, SignupRequest
from src.backend.db import User, Workspace, SessionLocal

class TestE2ESignupAudit(unittest.TestCase):

    def setUp(self):
        self.mock_db = MagicMock()
        self.mock_user = None
        self.mock_ws = None

    def test_e2e_zero_stripe_signup_flow(self):
        req = SignupRequest(username="beta_tester_pro", password="securepassword123")
        
        # Mock database user check returns None (new user)
        self.mock_db.query().filter().first.return_value = None

        res = signup_user(req, db=self.mock_db)

        # 1. Verify successful signup status
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["username"], "beta_tester_pro")
        self.assertEqual(res["subscription_tier"], "free")
        self.assertTrue(res["api_key"].startswith("nx_live_"))

        # 2. Verify DB commit was called for User and Workspace
        self.assertGreaterEqual(self.mock_db.add.call_count, 2)
        self.assertGreaterEqual(self.mock_db.commit.call_count, 2)

    def test_email_verification_flow(self):
        from src.backend.main import verify_email
        from src.backend.email_service import render_verification_email_html, send_verification_email
        
        req = SignupRequest(username="new_trial_user", password="password123")
        self.mock_db.query().filter().first.return_value = None

        res = signup_user(req, db=self.mock_db)
        self.assertFalse(res["email_verified"])
        self.assertEqual(res["sender"], "nexus@krusch.dev")
        self.assertIsNotNone(res["verification_link"])

        # Test verification HTML rendering
        html = render_verification_email_html("new_trial_user", "nx_v_test123", "https://krusch.dev/api/verify-email?token=nx_v_test123")
        self.assertIn("nexus@krusch.dev", html)
        self.assertIn("Verify Email Address", html)

        # Test verification endpoint
        mock_user = User(username="new_trial_user", email_verified=False, verification_token="nx_v_test123")
        self.mock_db.query().filter().first.return_value = mock_user

        v_res = verify_email(token="nx_v_test123", db=self.mock_db)
        self.assertEqual(v_res["status"], "success")
        self.assertTrue(mock_user.email_verified)
        self.assertIsNone(mock_user.verification_token)

    def test_default_pro_quota_assignment(self):
        ws = Workspace(name="test_beta_ws")
        self.assertEqual(ws.doc_limit, 5000)
        self.assertEqual(ws.query_limit_monthly, 50000)
        self.assertEqual(ws.queries_used, 0)

if __name__ == '__main__':
    unittest.main()
