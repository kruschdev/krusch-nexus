import unittest
from krusch_nexus.client import NexusClient


class TestConfirmationTokens(unittest.TestCase):
    def test_expected_release_and_purge_tokens(self):
        # SEC-01: Verify centralized token helpers generate canonical confirmation strings
        self.assertEqual(
            NexusClient._expected_release_token(" legal-ops "),
            "CONFIRM_RELEASE_HOLD_legal-ops",
        )
        self.assertEqual(
            NexusClient._expected_purge_token(" legal-ops "),
            "CONFIRM_PURGE_legal-ops",
        )


if __name__ == "__main__":
    unittest.main()
