"""
Unit tests for KruschNexus Security & Air-Gap Enforcement.
Verifies rejection of:
- Insecure default database password ('kruschpassword')
- Non-local cloud embedding providers without ALLOW_CLOUD=1
"""

import unittest
from krusch_nexus.config import NexusConfig
from krusch_nexus.exceptions import AirGapViolationError, ConfigurationError
from krusch_nexus.offline_check import verify_offline_environment


class TestSecurity(unittest.TestCase):

    def test_reject_default_kruschpassword(self):
        """Verify startup is refused when database_url contains 'kruschpassword'."""
        with self.assertRaises(ConfigurationError) as ctx:
            NexusConfig(database_url="postgresql://krusch:kruschpassword@localhost:5432/nexus_db")
        self.assertIn("Insecure default database password 'kruschpassword' detected", str(ctx.exception))

    def test_reject_cloud_provider_without_allow_cloud(self):
        """Verify startup is refused when embedding_provider != 'ollama' without ALLOW_CLOUD=1."""
        with self.assertRaises(AirGapViolationError) as ctx:
            NexusConfig(embedding_provider="openai", allow_cloud=False)
        self.assertIn("Insecure embedding provider 'openai' rejected", str(ctx.exception))

    def test_allow_cloud_override(self):
        """Verify cloud provider is permitted only when allow_cloud is explicitly True."""
        conf = NexusConfig(
            database_url="sqlite:///:memory:",
            embedding_provider="openai",
            allow_cloud=True
        )
        self.assertEqual(conf.embedding_provider, "openai")
        self.assertTrue(conf.allow_cloud)

    def test_offline_check_structure(self):
        """Verify offline verification returns expected structured report."""
        conf = NexusConfig(
            database_url="sqlite:///:memory:",
            ollama_url="http://127.0.0.1:11434"
        )
        report = verify_offline_environment(conf)
        self.assertIn("air_gap_secure", report)
        self.assertIn("ollama_is_local", report)
        self.assertTrue(report["ollama_is_local"])


if __name__ == "__main__":
    unittest.main()
