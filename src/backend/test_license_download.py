import os
import sys
import json
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ["DATABASE_URL"] = os.getenv("DBOS_DATABASE_URL", "postgresql://openclaw:openclaw_password@10.0.0.85:5434/kruschdb")
os.environ["DBOS_DATABASE_URL"] = os.environ["DATABASE_URL"]

import src.backend.test_stubs

from fastapi.testclient import TestClient
from src.backend.main import app

class TestLicenseAndDownloadEndpoints(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)

    def test_generate_and_verify_license(self):
        # 1. Generate license key
        gen_res = self.client.post("/api/license/generate")
        self.assertEqual(gen_res.status_code, 200)
        gen_data = gen_res.json()
        self.assertIn("license_key", gen_data)
        self.assertTrue(gen_data["license_key"].startswith("NEXUS-LIC-"))

        lic_key = gen_data["license_key"]

        # 2. Verify license key via GET
        ver_get_res = self.client.get(f"/api/license/verify?license_key={lic_key}")
        self.assertEqual(ver_get_res.status_code, 200)
        ver_get_data = ver_get_res.json()
        self.assertTrue(ver_get_data["valid"])

        # 3. Verify license key via POST
        ver_post_res = self.client.post("/api/license/verify", json={"license_key": lic_key})
        self.assertEqual(ver_post_res.status_code, 200)
        ver_post_data = ver_post_res.json()
        self.assertTrue(ver_post_data["valid"])

    def test_download_installer_script(self):
        res = self.client.get("/nexus/install.sh")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Installing Krusch-Nexus Business RAG & MCP Server", res.text)

    def test_download_package_info(self):
        res = self.client.get("/api/download/package")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("one_liner_cmd", data)
        self.assertIn("https://krusch.dev/nexus/install.sh", data["installer_url"])

if __name__ == "__main__":
    unittest.main()
