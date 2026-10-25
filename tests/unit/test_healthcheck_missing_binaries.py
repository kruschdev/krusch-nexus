"""
Unit tests verifying startup lifespan and healthcheck failure when required
host binaries (Poppler pdftotext/pdftoppm or Tesseract) are missing (BUG-01, TEST-01).
"""

import asyncio
import unittest
from unittest.mock import patch

import krusch_nexus.exceptions
from krusch_nexus.api import app, health_check, lifespan, verify_host_binaries


class TestHealthcheckMissingBinaries(unittest.TestCase):
    def test_missing_poppler_raises_configuration_error(self):
        """Verify missing pdftotext/pdftoppm raises ConfigurationError in health_check and lifespan."""
        def fake_which(cmd: str):
            if cmd in ("pdftotext", "pdftoppm"):
                return None
            return f"/usr/bin/{cmd}"

        with patch("shutil.which", side_effect=fake_which):
            with self.assertRaises(krusch_nexus.exceptions.ConfigurationError) as ctx:
                health_check()
            self.assertIn("Poppler", str(ctx.exception))

            with self.assertRaises(krusch_nexus.exceptions.ConfigurationError) as ctx_life:
                async def _run():
                    async with lifespan(app):
                        pass
                asyncio.run(_run())
            self.assertIn("Poppler", str(ctx_life.exception))

    def test_missing_tesseract_raises_configuration_error(self):
        """Verify missing tesseract raises ConfigurationError in health_check and verify_host_binaries."""
        def fake_which(cmd: str):
            if cmd == "tesseract":
                return None
            return f"/usr/bin/{cmd}"

        with patch("shutil.which", side_effect=fake_which):
            with self.assertRaises(krusch_nexus.exceptions.ConfigurationError) as ctx:
                verify_host_binaries()
            self.assertIn("Tesseract", str(ctx.exception))

            with self.assertRaises(krusch_nexus.exceptions.ConfigurationError) as ctx_hc:
                health_check()
            self.assertIn("Tesseract", str(ctx_hc.exception))


if __name__ == "__main__":
    unittest.main()
