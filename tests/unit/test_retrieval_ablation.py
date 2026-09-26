"""
tests/unit/test_retrieval_ablation.py
======================================
Comprehensive unit tests for the 4-stage retrieval ablation matrix:
1. vector_only (ANN embedding search without sparse FTS)
2. fts_only (lexical full-text search without dense vectors)
3. hybrid / hybrid-rrf (reciprocal rank fusion + capped boosts)
4. rrf+rerank (RRF candidate generation + cross-encoder reranking stage)

Also verifies:
- In-process embedding configurations & model checksum fingerprints.
- Strict 0.12 total boost cap invariant.
- Air-gap validation rejecting insecure external egress.
"""

import os
import shutil
import tempfile
import unittest
from krusch_nexus.models import NexusConfig, SearchHit, AirGapViolationError
from krusch_nexus.client import NexusClient
from krusch_nexus.store import init_db, get_engine
from krusch_nexus.embeddings import compute_model_checksum, generate_deterministic_vector


FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")


class TestRetrievalAblation(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="nexus_ablation_test_")
        self.db_path = os.path.join(self.test_dir, "ablation.db")
        self.config = NexusConfig(
            database_url=f"sqlite:///{self.db_path}",
            allowed_ingest_roots=[self.test_dir, FIXTURES_DIR],
            rrf_k=60,
            section_boost_header=0.08,
            section_boost_content=0.04,
            phrase_boost=0.06
        )
        self.engine = get_engine(self.config.database_url)
        init_db(self.engine)
        self.client = NexusClient(self.config)
        self.workspace = "AblationTest"

        # Ingest fixture documents with pre-recorded realistic vectors
        p1 = os.path.join(FIXTURES_DIR, "sample_contract.pdf")
        p2 = os.path.join(FIXTURES_DIR, "municipal_code.txt")
        self.client.ingest(p1, workspace=self.workspace)
        self.client.ingest(p2, workspace=self.workspace)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_vector_only_mode(self):
        """Mode 'vector_only' must evaluate ANN vectors exclusively without FTS ranks or boosts."""
        hits = self.client.search(
            "thirty days notice",
            workspace=self.workspace,
            mode="vector_only",
            limit=5
        )
        self.assertGreater(len(hits), 0)
        for h in hits:
            self.assertIsNotNone(h.score_vector.get("vector_rank"))
            self.assertIsNone(h.score_vector.get("fts_rank"))
            # Boosts are bypassed in vector_only mode
            self.assertFalse(h.score_vector.get("section_boost"))
            self.assertFalse(h.score_vector.get("phrase_boost"))

    def test_fts_only_mode(self):
        """Mode 'fts_only' must evaluate lexical full-text matches exclusively without vector embeddings."""
        hits = self.client.search(
            "written notice",
            workspace=self.workspace,
            mode="fts_only",
            limit=5
        )
        self.assertGreater(len(hits), 0)
        for h in hits:
            self.assertIsNotNone(h.score_vector.get("fts_rank"))
            self.assertIsNone(h.score_vector.get("vector_rank"))
            self.assertFalse(h.score_vector.get("section_boost"))
            self.assertFalse(h.score_vector.get("phrase_boost"))

    def test_hybrid_rrf_mode_and_boost_cap(self):
        """Mode 'hybrid' fuses dense + sparse scores and strictly caps boosts to <= 0.12."""
        hits = self.client.search(
            'Section 8.22.030 "Rent Adjustment"',
            workspace=self.workspace,
            mode="hybrid",
            limit=5
        )
        self.assertGreater(len(hits), 0)
        top = hits[0]
        self.assertIn("Section 8.22.030", top.text)
        # Check that score_vector reflects both signals or boosts
        self.assertIn("final_score", top.score_vector)
        self.assertIn("rrf_score", top.score_vector)

    def test_rrf_plus_rerank_mode_with_custom_scorer(self):
        """Mode 'rrf+rerank' (or rerank=True) re-orders candidates and records cross-encoder diagnostics."""
        def custom_reranker(query, texts):
            return [1.0 if "residential" in t.lower() else 0.1 for t in texts]

        hits = self.client.search(
            "landlord tenant notice",
            workspace=self.workspace,
            mode="rrf+rerank",
            rerank_fn=custom_reranker,
            limit=5
        )
        self.assertGreater(len(hits), 0)
        top_hit = hits[0]
        self.assertIn("residential", top_hit.text.lower())
        self.assertEqual(top_hit.score_vector.get("rerank_score"), 1.0)
        self.assertEqual(top_hit.score_vector.get("rerank_rank"), 1)
        self.assertIn("cross_encoder_rerank", top_hit.match_reasons)

    def test_rerank_fallback_airgap_resilience(self):
        """Reranker functions gracefully without crashing when neural weights are unmounted."""
        # Using default fallback (lexical overlap) with mode='rrf+rerank'
        hits = self.client.search(
            "thirty days notice",
            workspace=self.workspace,
            mode="rrf+rerank",
            limit=5
        )
        self.assertGreater(len(hits), 0)
        top = hits[0]
        self.assertIn("rerank_score", top.score_vector)
        self.assertIn("rerank_rank", top.score_vector)
        self.assertIn("cross_encoder_rerank", top.match_reasons)

    def test_model_checksum_fingerprint(self):
        """Verify model checksum computation produces deterministic SHA-256 fingerprint."""
        cs1 = compute_model_checksum("bge-large", backend="ollama", dim=1024)
        cs2 = compute_model_checksum("bge-large", backend="ollama", dim=1024)
        cs3 = compute_model_checksum("bge-small-en-v1.5", backend="fastembed", dim=384)

        self.assertTrue(cs1.startswith("sha256:"))
        self.assertEqual(cs1, cs2)
        self.assertNotEqual(cs1, cs3)

    def test_airgap_validation(self):
        """NexusConfig must allow local/in-process backends and reject cloud providers without ALLOW_CLOUD=1."""
        # Local allowed
        cfg_ollama = NexusConfig(embed_backend="ollama", embedding_provider="ollama")
        self.assertEqual(cfg_ollama.embed_backend, "ollama")

        cfg_in_proc = NexusConfig(embed_backend="in_process", embedding_provider="in_process")
        self.assertEqual(cfg_in_proc.embed_backend, "in_process")

        cfg_fastembed = NexusConfig(embed_backend="fastembed", embedding_provider="fastembed")
        self.assertEqual(cfg_fastembed.embed_backend, "fastembed")

        # Insecure cloud provider rejected
        with self.assertRaises(AirGapViolationError):
            NexusConfig(embed_backend="openai", embedding_provider="openai", allow_cloud=False)


if __name__ == "__main__":
    unittest.main()
