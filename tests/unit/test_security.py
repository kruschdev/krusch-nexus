"""
Unit tests for KruschNexus Security & Air-Gap Enforcement.
Verifies rejection of:
- Insecure default database password ('kruschpassword')
- Non-local cloud embedding providers without ALLOW_CLOUD=1
- Structured doctor diagnostics report
"""

import unittest
from krusch_nexus.models import NexusConfig
from krusch_nexus.exceptions import AirGapViolationError, ConfigurationError
from krusch_nexus.cli import run_doctor_checks


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

    def test_reject_cloud_embed_endpoint_without_allow_cloud(self):
        """Verify AirGapViolationError is raised on remote cloud embed endpoints."""
        with self.assertRaises(AirGapViolationError) as ctx:
            NexusConfig(ollama_url="https://api.openai.com/v1", allow_cloud=False)
        self.assertIn("Air-gap violation", str(ctx.exception))

    def test_reject_cloud_embed_model_without_allow_cloud(self):
        """Verify AirGapViolationError is raised on proprietary cloud embed models."""
        with self.assertRaises(AirGapViolationError) as ctx:
            NexusConfig(embed_model="text-embedding-3-small", allow_cloud=False)
        self.assertIn("Air-gap violation", str(ctx.exception))

    def test_doctor_fails_on_missing_token_in_production(self):
        """Verify nexus doctor fails when API token is missing in production."""
        conf = NexusConfig(
            database_url="sqlite:///:memory:",
            environment="production",
            api_token=None
        )
        report = run_doctor_checks(conf)
        self.assertEqual(report["api_token"]["status"], "FAIL")
        self.assertFalse(report["healthy"])

    def test_doctor_fails_on_wildcard_bind_in_production(self):
        """Verify nexus doctor fails on 0.0.0.0 bind in production."""
        conf = NexusConfig(
            database_url="sqlite:///:memory:",
            environment="production",
            api_host="0.0.0.0",
            api_token="prod-secure-token-12345"
        )
        report = run_doctor_checks(conf)
        self.assertEqual(report["localhost_bind"]["status"], "FAIL")
        self.assertFalse(report["healthy"])

    def test_doctor_checks_structure(self):
        """Verify nexus doctor diagnostics returns expected structured report."""
        conf = NexusConfig(
            database_url="sqlite:///:memory:",
            ollama_url="http://127.0.0.1:11434"
        )
        report = run_doctor_checks(conf)
        self.assertIn("air_gap", report)
        self.assertIn("air_gap_policy", report)
        self.assertIn("localhost_bind", report)
        self.assertIn("api_token", report)
        self.assertIn("poppler", report)
        self.assertIn("tesseract", report)
        self.assertIn("database", report)
        self.assertIn("ollama", report)
        self.assertIn("healthy", report)


if __name__ == "__main__":
    unittest.main()
