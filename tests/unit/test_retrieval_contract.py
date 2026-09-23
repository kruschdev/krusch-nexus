"""
tests/unit/test_retrieval_contract.py
=====================================
Unit tests for KruschNexus retrieval enhancements:
- SQL-level SearchFilter predicates (page, doc_id, doc_type, filename)
- Normalized section query boosting (§1950.5, Section 1950.5, sec. 1950.5)
- Capped boosts to prevent drowning semantic hits
- SearchTrace explainability logging
"""

import os
import shutil
import tempfile
import unittest

from krusch_nexus import NexusClient, NexusConfig, DocType, SearchFilter
from krusch_nexus.store import init_db, get_engine, SearchTrace, get_db_session

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")


class TestRetrievalContract(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="nexus_retrieval_unit_")
        cls.db_path = os.path.join(cls.temp_dir, "test.db")
        cls.config = NexusConfig(
            database_url=f"sqlite:///{cls.db_path}",
            allowed_ingest_roots=[FIXTURES_DIR, cls.temp_dir]
        )
        cls.engine = get_engine(cls.config.database_url)
        init_db(cls.engine)
        cls.nexus = NexusClient(cls.config)

        # Ingest multi-page contract and municipal code
        p1 = os.path.join(FIXTURES_DIR, "sample_contract.pdf")
        p2 = os.path.join(FIXTURES_DIR, "municipal_code.txt")
        cls.rep1 = cls.nexus.ingest(p1, workspace="RetrievalTest", doc_type=DocType.AUTHORITY)
        cls.rep2 = cls.nexus.ingest(p2, workspace="RetrievalTest", doc_type=DocType.AUTHORITY)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_sql_filter_page(self):
        """Verify page filter restricts results to exact page at SQL level."""
        # Page 1 only
        hits_p1 = self.nexus.search(
            query="lease",
            workspace="RetrievalTest",
            filters={"page": 1}
        )
        self.assertGreater(len(hits_p1), 0)
        for h in hits_p1:
            self.assertEqual(h.page_number, 1)

        # Page 2 only
        hits_p2 = self.nexus.search(
            query="cure breach thirty days",
            workspace="RetrievalTest",
            filters={"page": 2}
        )
        self.assertGreater(len(hits_p2), 0)
        for h in hits_p2:
            self.assertEqual(h.page_number, 2)

    def test_sql_filter_doc_id(self):
        """Verify doc_id filter restricts results to specified document."""
        doc1_id = self.rep1.document_id
        hits = self.nexus.search(
            query="lease",
            workspace="RetrievalTest",
            filters={"doc_id": doc1_id}
        )
        self.assertGreater(len(hits), 0)
        for h in hits:
            self.assertEqual(h.document_id, doc1_id)

    def test_normalized_section_boost(self):
        """Verify normalized section boost matches §1950.5, Section 1950.5, and sec. 1950.5."""
        variations = ["§ 1950.5", "§1950.5", "Section 1950.5", "sec. 1950.5"]
        for v in variations:
            hits = self.nexus.search(v, workspace="RetrievalTest", limit=3)
            self.assertGreater(len(hits), 0, f"Query '{v}' returned no hits")
            top = hits[0]
            self.assertEqual(top.filename, "municipal_code.txt")
            self.assertTrue(top.section_boost or top.lexical_boost, f"Query '{v}' failed to trigger section boost")

    def test_search_trace_logging(self):
        """Verify search execution logs explainability fuse without document text."""
        self.nexus.search("thirty days notice", workspace="RetrievalTest", limit=3)
        with get_db_session(self.engine) as sess:
            traces = sess.query(SearchTrace).all()
            self.assertGreater(len(traces), 0)
            t = traces[-1]
            self.assertIsNotNone(t.query_hash)
            self.assertIsNotNone(t.fused_ranks)
            self.assertGreaterEqual(t.duration_ms, 0.0)
            # Ensure NO document content is stored in search trace
            trace_str = f"{t.dense_ranks} {t.sparse_ranks} {t.fused_ranks} {t.boosts_applied}"
            self.assertNotIn("COMMERCIAL LEASE AGREEMENT", trace_str)
            self.assertNotIn("thirty days notice", trace_str)

    def test_zero_matches_returns_empty_without_hallucinating_recent_chunks(self):
        """Verify queries with zero matching keywords/vectors return empty list instead of arbitrary recent chunks."""
        hits = self.nexus.search("xyzzyqwertyfoobar nonmatching nonsense query", workspace="RetrievalTest")
        self.assertEqual(len(hits), 0, "Zero-match query must return [] without hallucinating recent chunks.")

    def test_bounded_lru_cache_eviction(self):
        """Verify BoundedLRUCache enforces capacity limit and evicts oldest unused keys."""
        from krusch_nexus.retrieve import BoundedLRUCache
        cache = BoundedLRUCache(maxsize=3)
        cache["q1"] = [0.1, 0.2]
        cache["q2"] = [0.3, 0.4]
        cache["q3"] = [0.5, 0.6]
        self.assertEqual(len(cache), 3)

        # Access q1 to make it recently used
        _ = cache["q1"]

        # Insert q4 -> q2 (oldest) must be evicted
        cache["q4"] = [0.7, 0.8]
        self.assertEqual(len(cache), 3)
        self.assertIn("q1", cache)
        self.assertIn("q3", cache)
        self.assertIn("q4", cache)
        self.assertNotIn("q2", cache)

    def test_dimension_drift_rejection(self):
        """Verify ModelDimensionDriftError is raised when embedding dimensions mismatch."""
        from krusch_nexus.exceptions import ModelDimensionDriftError
        from krusch_nexus.retrieve import retrieve

        # Mock embedding function returning 768d instead of configured 1024d
        mock_bad_embed = lambda q: [0.1] * 768

        with get_db_session(self.engine) as sess:
            from krusch_nexus.store import Workspace
            ws = sess.query(Workspace).filter_by(name=self.rep1.workspace).first()
            ws_id = ws.id
            with self.assertRaises(ModelDimensionDriftError):
                retrieve(
                    query="dimension_drift_probe",
                    workspace_id=ws_id,
                    db=sess,
                    embed_fn=mock_bad_embed,
                    config=self.config
                )

    def test_invalid_and_oversized_header_regex_rejection(self):
        """Verify unbounded or syntactically invalid header_regex patterns are rejected."""
        from krusch_nexus.exceptions import NexusError

        # Regex too long (>120 chars)
        oversized = "a" * 150
        with self.assertRaises(NexusError):
            self.nexus.search("lease", workspace="RetrievalTest", filters={"header_regex": oversized})

        # Syntactically invalid regex
        invalid_pattern = "[unclosed_bracket"
        with self.assertRaises(NexusError):
            self.nexus.search("lease", workspace="RetrievalTest", filters={"header_regex": invalid_pattern})

    def test_valid_header_regex_filtering(self):
        """Verify valid header_regex correctly filters chunks by header or locator."""
        hits_filtered = self.nexus.search(
            "lease",
            workspace="RetrievalTest",
            filters={"header_regex": r"Permitted Use"}
        )
        self.assertGreater(len(hits_filtered), 0)
        for h in hits_filtered:
            self.assertIn("Permitted Use", f"{h.header} {h.locator}")
