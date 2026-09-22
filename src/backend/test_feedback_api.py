import os
import sys
import unittest
from unittest.mock import MagicMock

# Set Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import src.backend.test_stubs

from src.backend.main import submit_beta_feedback, FeedbackRequest

class TestFeedbackApi(unittest.TestCase):

    def test_submit_beta_feedback(self):
        req = FeedbackRequest(
            user_email="tester@krusch.dev",
            category="bug",
            message="Testing feedback submission endpoint."
        )
        res = submit_beta_feedback(req)
        self.assertEqual(res["status"], "success")
        self.assertIn("Thank you for your feedback", res["message"])

if __name__ == '__main__':
    unittest.main()
