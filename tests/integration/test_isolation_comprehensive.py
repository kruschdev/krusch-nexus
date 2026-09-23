"""
tests/integration/test_isolation_comprehensive.py
=================================================
Exhaustive multi-tenant isolation, authorization ACL, and security boundary test suite:
1. Two distinct tokens across two isolated workspaces across 100 alternating queries
   via REST and FastMCP, verifying 0.0000% cross-tenant leakage.
2. Cross-workspace access denials (token A accessing workspace B returns HTTP 403 / MCP access error).
3. Default-deny enforcement when NEXUS_ENV != "dev" and NEXUS_API_TOKEN is missing.
4. Tar-slip malicious archive rejection via PathSandboxError.
5. Production profile doctor check asserting localhost bind and secure token.
"""

import io
import os
import tarfile
import tempfile
import shutil
import unittest
from fastapi.testclient import TestClient

from krusch_nexus.models import NexusConfig, DocType
from krusch_nexus.store import init_db, get_engine
from krusch_nexus.client import NexusClient
from krusch_nexus.exceptions import PathSandboxError
from krusch_nexus.cli import run_doctor_checks
import krusch_nexus.api as api_module
from krusch_nexus.mcp import set_client, nexus_search_corpus, nexus_list_workspaces


class TestIsolationComprehensive(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nexus_isol_test_")
        self.db_path = os.path.join(self.temp_dir, "test_isol.db")

        self.token_alpha = "tok-alpha-matter-1111"
        self.token_beta = "tok-beta-matter-2222"
        self.ws_alpha = "TenantAlpha"
        self.ws_beta = "TenantBeta"

        self.config = NexusConfig(
            database_url=f"sqlite:///{self.db_path}",
            allowed_ingest_roots=[self.temp_dir],
            api_token="master-admin-token-xyz",
            token_workspaces={
                self.token_alpha: [self.ws_alpha],
                self.token_beta: [self.ws_beta]
            }
        )
        self.engine = get_engine(self.config.database_url)
        init_db(self.engine)
        self.nexus = NexusClient(self.config)

        # Wire up REST API
        api_module.config = self.config
        api_module.client = self.nexus
        self.rest_client = TestClient(api_module.app)

        # Wire up FastMCP
        set_client(self.nexus)

        # Ingest distinctive documents into each workspace
        file_alpha = os.path.join(self.temp_dir, "alpha_contract.txt")
        with open(file_alpha, "w") as f:
            f.write("CONFIDENTIAL ALPHA: Project Apollo operative agreement. Liquidated damages shall be $5,000.\n")
        self.nexus.ingest(file_alpha, workspace=self.ws_alpha, doc_type=DocType.AUTHORITY)

        file_beta = os.path.join(self.temp_dir, "beta_contract.txt")
        with open(file_beta, "w") as f:
            f.write("CONFIDENTIAL BETA: Project Boreas operative agreement. Liquidated damages shall be $10,000.\n")
        self.nexus.ingest(file_beta, workspace=self.ws_beta, doc_type=DocType.AUTHORITY)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_one_hundred_searches_zero_leakage_rest_and_mcp(self):
        """
        Execute 100 alternating queries across REST and FastMCP.
        Assert that 0 hits ever leak across workspaces and cross-tenant attempts are denied.
        """
        import json

        queries = [
            "liquidated damages",
            "Project Apollo",
            "Project Boreas",
            "CONFIDENTIAL",
            "operative agreement"
        ]

        total_checks = 0
        leaked_hits = 0

        # Run 100 queries (50 alpha, 50 beta)
        for i in range(50):
            q = queries[i % len(queries)]

            # 1. Alpha searches via REST
            headers_alpha = {"Authorization": f"Bearer {self.token_alpha}"}
            resp_alpha = self.rest_client.post(
                "/v1/search",
                json={"query": q, "workspace": self.ws_alpha, "limit": 5},
                headers=headers_alpha
            )
            self.assertEqual(resp_alpha.status_code, 200)
            hits_alpha = resp_alpha.json()
            for h in hits_alpha:
                total_checks += 1
                if h.get("workspace") != self.ws_alpha:
                    leaked_hits += 1
                self.assertNotIn("Project Boreas", h["text"])

            # 2. Beta searches via REST
            headers_beta = {"Authorization": f"Bearer {self.token_beta}"}
            resp_beta = self.rest_client.post(
                "/v1/search",
                json={"query": q, "workspace": self.ws_beta, "limit": 5},
                headers=headers_beta
            )
            self.assertEqual(resp_beta.status_code, 200)
            hits_beta = resp_beta.json()
            for h in hits_beta:
                total_checks += 1
                if h.get("workspace") != self.ws_beta:
                    leaked_hits += 1
                self.assertNotIn("Project Apollo", h["text"])

            # 3. Alpha searches via FastMCP
            mcp_raw_alpha = nexus_search_corpus(
                query=q,
                workspace_name=self.ws_alpha,
                token=self.token_alpha
            )
            mcp_data_alpha = json.loads(mcp_raw_alpha)
            self.assertEqual(mcp_data_alpha["status"], "success")
            for h in mcp_data_alpha.get("results", []):
                total_checks += 1
                if h.get("workspace") != self.ws_alpha:
                    leaked_hits += 1
                self.assertNotIn("Project Boreas", h["text"])

            # 4. Beta searches via FastMCP
            mcp_raw_beta = nexus_search_corpus(
                query=q,
                workspace_name=self.ws_beta,
                token=self.token_beta
            )
            mcp_data_beta = json.loads(mcp_raw_beta)
            self.assertEqual(mcp_data_beta["status"], "success")
            for h in mcp_data_beta.get("results", []):
                total_checks += 1
                if h.get("workspace") != self.ws_beta:
                    leaked_hits += 1
                self.assertNotIn("Project Apollo", h["text"])

            # 5. Cross-tenant rejection checks: Alpha trying to access Beta
            cross_rest = self.rest_client.post(
                "/v1/search",
                json={"query": q, "workspace": self.ws_beta, "limit": 5},
                headers=headers_alpha
            )
            self.assertEqual(cross_rest.status_code, 403)

            cross_mcp = json.loads(nexus_search_corpus(
                query=q,
                workspace_name=self.ws_beta,
                token=self.token_alpha
            ))
            self.assertEqual(cross_mcp["status"], "error")
            self.assertIn("Access denied", cross_mcp["error"])

        self.assertGreater(total_checks, 0)
        self.assertEqual(leaked_hits, 0, f"Tenant leakage detected: {leaked_hits} hits leaked across tenants!")
        print(f"\n[TestIsolationComprehensive] Completed {total_checks} tenant checks across REST/MCP. Leakage: 0.0000%")

    def test_default_deny_when_env_non_dev(self):
        """Verify unauthenticated requests are rejected with 401 when NEXUS_ENV != dev."""
        prod_config = NexusConfig(
            database_url=f"sqlite:///{self.db_path}",
            environment="prod",
            api_token="prod-secure-token-999"
        )
        api_module.config = prod_config
        api_module.client = NexusClient(prod_config)
        prod_client = TestClient(api_module.app)

        # 1. No authorization header -> 401
        resp = prod_client.get("/v1/workspaces")
        self.assertEqual(resp.status_code, 401)

        # 2. Invalid token -> 401
        resp2 = prod_client.get("/v1/workspaces", headers={"Authorization": "Bearer invalid-token"})
        self.assertEqual(resp2.status_code, 401)

        # 3. Valid token -> 200
        resp3 = prod_client.get("/v1/workspaces", headers={"Authorization": "Bearer prod-secure-token-999"})
        self.assertEqual(resp3.status_code, 200)

    def test_tar_slip_malicious_archive_rejected(self):
        """Verify that importing an archive with malicious path traversal raises PathSandboxError."""
        evil_tar_path = os.path.join(self.temp_dir, "evil_tarslip.tar.gz")

        # Create in-memory tar.gz with path traversal member
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            # Safe member
            ws_meta = b'{"name": "EvilWorkspace", "description": "Attack vector"}'
            info_safe = tarfile.TarInfo(name="workspace.json")
            info_safe.size = len(ws_meta)
            tar.addfile(info_safe, io.BytesIO(ws_meta))

            # Malicious member attempting traversal outside staging directory
            payload = b"root:x:0:0:root:/root:/bin/bash\n"
            info_evil = tarfile.TarInfo(name="../../etc/shadow")
            info_evil.size = len(payload)
            tar.addfile(info_evil, io.BytesIO(payload))

        with open(evil_tar_path, "wb") as f:
            f.write(buf.getvalue())

        # Attempt import
        with self.assertRaises(PathSandboxError) as ctx:
            self.nexus.import_workspace(evil_tar_path, target_workspace="ImportEvil")

        self.assertIn("Tar Slip attempt detected", str(ctx.exception))

    def test_doctor_production_profile_validation(self):
        """Verify 'nexus doctor --profile prod' enforces strict localhost and token criteria."""
        # Unconfigured token in prod profile
        conf_no_token = NexusConfig(
            database_url=f"sqlite:///{self.db_path}",
            environment="dev",
            api_token="",
            api_host="127.0.0.1"
        )
        res_no_token = run_doctor_checks(conf_no_token, profile="prod")
        self.assertEqual(res_no_token["api_token"]["status"], "FAIL")
        self.assertFalse(res_no_token["healthy"])

        # Non-localhost bind in prod profile
        conf_bad_host = NexusConfig(
            database_url=f"sqlite:///{self.db_path}",
            environment="prod",
            api_token="super-secret-token",
            api_host="0.0.0.0"
        )
        res_bad_host = run_doctor_checks(conf_bad_host, profile="prod")
        self.assertEqual(res_bad_host["localhost_bind"]["status"], "FAIL")
        self.assertFalse(res_bad_host["healthy"])


if __name__ == "__main__":
    unittest.main()
