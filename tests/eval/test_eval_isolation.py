"""
tests/eval/test_eval_isolation.py
=================================
eval_isolation: Multi-workspace property test asserting zero cross-workspace leakage.
Ingests overlapping and identical text into multiple distinct workspaces and asserts
100.00% tenant isolation across extensive search matrices.
"""

import os
import shutil
import tempfile
import unittest

from krusch_nexus import NexusClient, NexusConfig, DocType
from krusch_nexus.store import init_db, get_engine

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")


class TestEvalIsolation(unittest.TestCase):
    """
    eval_isolation suite:
    Property test guaranteeing strict tenant isolation:
    For all queries Q and workspaces W_i:
        search(Q, workspace=W_i) -> subset of chunks belonging strictly to W_i.
    """

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="nexus_eval_isolation_")
        cls.db_path = os.path.join(cls.temp_dir, "isolation.db")
        cls.config = NexusConfig(
            database_url=f"sqlite:///{cls.db_path}",
            allowed_ingest_roots=[FIXTURES_DIR, cls.temp_dir]
        )
        cls.engine = get_engine(cls.config.database_url)
        init_db(cls.engine)
        cls.nexus = NexusClient(cls.config)

        # Create 5 distinct tenant workspaces
        cls.workspaces = [
            "Tenant_Alpha_Matter_101",
            "Tenant_Beta_Matter_202",
            "Tenant_Gamma_Matter_303",
            "Tenant_Delta_Matter_404",
            "Tenant_Epsilon_Matter_505"
        ]

        # Ingest documents with overlapping statutory terms into each workspace
        fixture_file = os.path.join(FIXTURES_DIR, "municipal_code.txt")
        for ws in cls.workspaces:
            cls.nexus.ingest(
                filepath=fixture_file,
                workspace=ws,
                doc_type=DocType.AUTHORITY,
                archive=False
            )

        # Ingest distinctive secret files into specific workspaces
        for idx, ws in enumerate(cls.workspaces):
            secret_file = os.path.join(cls.temp_dir, f"secret_token_{idx}.txt")
            with open(secret_file, "w") as f:
                f.write(f"CONFIDENTIAL TOKEN FOR {ws}: SECRET_KEY_{idx * 9999}\nOperative provisions.")
            cls.nexus.ingest(
                filepath=secret_file,
                workspace=ws,
                doc_type=DocType.WORK_PRODUCT,
                archive=False
            )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_cross_workspace_leakage_property(self):
        """
        Property Test:
        Assert that for any query run against workspace W, 100% of returned hits
        belong strictly to W, with exactly 0.00% leakage from other tenants.
        """
        test_queries = [
            "§ 1950.5 Security Deposits and Tenant Protections",
            "Section 8.22.030 Rent Adjustment Program",
            "CONFIDENTIAL TOKEN SECRET_KEY",
            "statute of limitations rent disputes",
            "Just Cause for Eviction Ordinance"
        ]

        total_checks = 0
        leaked_hits = 0

        for ws in self.workspaces:
            for q in test_queries:
                hits = self.nexus.search(q, workspace=ws, limit=10)
                for hit in hits:
                    total_checks += 1
                    if hit.workspace != ws:
                        leaked_hits += 1

        leakage_rate = (leaked_hits / total_checks) if total_checks > 0 else 0.0

        print("\n=== EVAL_ISOLATION PROPERTY TEST RESULTS ===")
        print(f"Total Tenant Checks:        {total_checks}")
        print(f"Cross-Workspace Leakage:    {leakage_rate:.4%} ({leaked_hits}/{total_checks})")
        print("============================================\n")

        self.assertEqual(leaked_hits, 0, f"Critical isolation failure: {leaked_hits} hits leaked across workspaces!")
        self.assertEqual(leakage_rate, 0.0, "Cross-workspace leakage must be exactly 0.00%")


if __name__ == "__main__":
    unittest.main()
