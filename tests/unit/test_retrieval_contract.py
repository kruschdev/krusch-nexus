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

        def mock_bad_embed(q):
            return [0.1] * 768

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

    def test_invalid_input_error_on_empty_query_and_rerank(self):
        """BUG-01: Verify InvalidInputError is raised when retrieve receives empty query or compute_local_rerank_scores receives empty texts."""
        from krusch_nexus.exceptions import InvalidInputError
        from krusch_nexus.retrieve import retrieve, compute_local_rerank_scores

        with self.assertRaises(InvalidInputError):
            compute_local_rerank_scores("query", [])

        with get_db_session(self.engine) as sess:
            from krusch_nexus.store import Workspace
            ws = sess.query(Workspace).filter_by(name=self.rep1.workspace).first()
            with self.assertRaises(InvalidInputError):
                retrieve(query="   ", workspace_id=ws.id, db=sess, config=self.config)

    def test_score_vector_presence_on_search_hits(self):
        """Verify every SearchHit returns an explicit score_vector breakdown."""
        hits = self.nexus.search(
            "lease agreement",
            workspace="RetrievalTest"
        )
        self.assertGreater(len(hits), 0)
        for h in hits:
            self.assertIsInstance(h.score_vector, dict)
            self.assertIn("final_score", h.score_vector)
            self.assertIn("vector_rank", h.score_vector)
            self.assertIn("fts_rank", h.score_vector)
            self.assertIn("section_boost", h.score_vector)
            self.assertIn("phrase_boost", h.score_vector)

    def test_dummy_embed_backend_standalone_pipeline(self):
        """Verify embed_backend='dummy' generates valid 1024d vectors and searches offline."""
        from krusch_nexus.embeddings import generate_deterministic_vector
        # 1. Deterministic vector unit tests
        v1 = generate_deterministic_vector("test content", dim=1024)
        v2 = generate_deterministic_vector("test content", dim=1024)
        v3 = generate_deterministic_vector("different content", dim=1024)
        self.assertEqual(len(v1), 1024)
        self.assertEqual(v1, v2)
        self.assertNotEqual(v1, v3)

        # 2. Pipeline test with dummy backend
        dummy_dir = tempfile.mkdtemp(prefix="nexus_dummy_test_")
        try:
            cfg = NexusConfig(
                database_url=f"sqlite:///{dummy_dir}/dummy.db",
                allowed_ingest_roots=[dummy_dir],
                embed_backend="dummy",
                embedding_dim=1024
            )
            eng = get_engine(cfg.database_url)
            init_db(eng)
            client = NexusClient(cfg)

            doc_path = os.path.join(dummy_dir, "dummy_doc.txt")
            with open(doc_path, "w") as f:
                f.write("Deterministic dummy vector test without Ollama daemon.\n")

            rep = client.ingest(doc_path, workspace="DummyWS")
            self.assertEqual(rep.status, "completed")
            self.assertEqual(rep.chunks, 1)

            results = client.search("Deterministic dummy", workspace="DummyWS")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].filename, "dummy_doc.txt")
            self.assertIsNotNone(results[0].score_vector)
        finally:
            shutil.rmtree(dummy_dir, ignore_errors=True)

    def test_retrieval_ablation_switch(self):
        """Verify that mode parameter controls scoring: vector_only, fts_only, rrf_only, hybrid."""
        q = "thirty days notice"
        ws = "RetrievalTest"

        # 1. vector_only
        v_hits = self.nexus.search(q, workspace=ws, mode="vector_only", limit=5)
        self.assertGreater(len(v_hits), 0)

        # 2. fts_only
        f_hits = self.nexus.search(q, workspace=ws, mode="fts_only", limit=5)
        self.assertGreater(len(f_hits), 0)

        # 3. rrf_only
        r_hits = self.nexus.search(q, workspace=ws, mode="rrf_only", limit=5)
        self.assertGreater(len(r_hits), 0)

        # 4. hybrid (default)
        h_hits = self.nexus.search(q, workspace=ws, mode="hybrid", limit=5)
        self.assertGreater(len(h_hits), 0)

    def test_explain_scorecard_diagnostics(self):
        """Verify client.explain returns structured scorecard with rank diagnostics."""
        expl = self.nexus.explain("thirty days notice", workspace="RetrievalTest", limit=3)
        self.assertEqual(expl["query"], "thirty days notice")
        self.assertEqual(expl["workspace"], "RetrievalTest")
        self.assertEqual(expl["mode"], "hybrid")
        self.assertIn("latency_ms", expl)
        self.assertGreater(expl["hits_count"], 0)

        top_hit = expl["hits"][0]
        self.assertEqual(top_hit["rank"], 1)
        self.assertIn("score", top_hit)
        self.assertIn("citation", top_hit)
        self.assertIn("match_reasons", top_hit)

    def test_header_regex_fuzzing_and_re_dos_protection(self):
        """Verify header_regex filter handles adversarial inputs, syntax errors, and excessive length safely."""
        from krusch_nexus.exceptions import NexusError

        # 1. Invalid regex syntax: must raise NexusError (safe rejection, not unhandled crash)
        with self.assertRaises(NexusError):
            self.nexus.search("lease", workspace="RetrievalTest", filters={"header_regex": "[invalid(regex"})

        # 2. Overly long regex pattern (>120 characters): rejected with NexusError
        huge_regex = "a" * 1000
        with self.assertRaises(NexusError):
            self.nexus.search("lease", workspace="RetrievalTest", filters={"header_regex": huge_regex})

        # 3. Valid complex regex pattern: executes safely without ReDoS
        res = self.nexus.search("lease", workspace="RetrievalTest", filters={"header_regex": r"(?:Section|Article)\s+\d+"})
        self.assertIsInstance(res, list)

    def test_include_low_ocr_filtering(self):
        """Verify low-confidence OCR quarantined chunks are excluded by default and included with flag."""
        # 1. Ingest a document containing low OCR confidence quarantined page text
        quarantine_file = os.path.join(self.temp_dir, "quarantined_notice.txt")
        with open(quarantine_file, "w", encoding="utf-8") as f:
            f.write("Notice of Inspection\n[Scanned page 1 - low OCR confidence quarantined]\nUnreadable smear artifact.")

        self.nexus.ingest(quarantine_file, workspace="RetrievalTest", doc_type=DocType.GENERAL)

        # 2. Search without include_low_ocr (default False): must NOT return the quarantined chunk
        hits_default = self.nexus.search("quarantined", workspace="RetrievalTest", include_low_ocr=False)
        self.assertFalse(any("quarantined" in (h.filename or "") for h in hits_default), "Quarantined file must be excluded by default")
        self.assertFalse(any("low ocr confidence quarantined" in h.text.lower() for h in hits_default))

        # Explicit filename filter with include_low_ocr=False: must return 0
        hits_filtered_fn = self.nexus.search("quarantined", workspace="RetrievalTest", filters={"filename": "quarantined_notice.txt"}, include_low_ocr=False)
        self.assertEqual(len(hits_filtered_fn), 0, "Quarantined document must return 0 hits when include_low_ocr=False")

        # 3. Search with SearchFilter(include_low_ocr=False): must NOT return the quarantined chunk
        hits_filter_false = self.nexus.search("quarantined", workspace="RetrievalTest", filters=SearchFilter(filename="quarantined_notice.txt", include_low_ocr=False))
        self.assertEqual(len(hits_filter_false), 0, "SearchFilter(include_low_ocr=False) must exclude quarantined pages")

        # 4. Search with include_low_ocr=True: MUST return the quarantined chunk
        hits_included = self.nexus.search("quarantined", workspace="RetrievalTest", filters={"filename": "quarantined_notice.txt"}, include_low_ocr=True)
        self.assertGreater(len(hits_included), 0, "Quarantined low-OCR pages must be returned when include_low_ocr=True")
        self.assertIn("quarantined", hits_included[0].text.lower())
        self.assertEqual(hits_included[0].filename, "quarantined_notice.txt")

        # 5. Search with filters dict {"include_low_ocr": True}
        hits_dict = self.nexus.search("quarantined", workspace="RetrievalTest", filters={"filename": "quarantined_notice.txt", "include_low_ocr": True})
        self.assertGreater(len(hits_dict), 0, "filters dict include_low_ocr=True must return quarantined pages")

    def test_format_chunk_for_reranking_contextual_headers_preserve_raw_text(self):
        """Verify format_chunk_for_reranking prepends Document + Section headers without mutating raw text."""
        from krusch_nexus import format_chunk_for_reranking

        raw_body = "Receiving Party shall protect Confidential Information for three (3) years."
        formatted = format_chunk_for_reranking(
            text=raw_body,
            filename="contractnli/Acme_Mutual_NDA_2025.txt",
            header="Section 4.2",
            locator="Page 3 > Article IV > Section 4.2",
            heading_path='["Page 3", "Article IV", "Section 4.2"]',
        )
        self.assertTrue(formatted.startswith("Document: contractnli Acme Mutual NDA 2025\n"))
        self.assertIn("Section: Article IV > Section 4.2\n", formatted)
        self.assertTrue(formatted.endswith(raw_body))
        self.assertNotIn("Page 3", formatted)

    def test_cross_encoder_singleton_cache_and_causal_lm_pad_token_guard(self):
        """Verify get_cached_cross_encoder caches singletons and assigns eos_token when pad_token is None."""
        import sys
        import types
        from unittest.mock import MagicMock
        from krusch_nexus.retrieve import get_cached_cross_encoder, _CROSS_ENCODER_CACHE

        fake_torch = types.ModuleType("torch")
        fake_torch.float16 = "float16"
        fake_torch.cuda = types.SimpleNamespace(is_available=lambda: True)
        fake_torch.nn = types.SimpleNamespace(Identity=lambda: "identity_fn")

        created_instances = []

        class FakeCrossEncoder:
            def __init__(self, model_id, device=None, max_length=None, activation_fn=None, model_kwargs=None):
                self.model_id = model_id
                self.device = device
                self.max_length = max_length
                self.model_kwargs = model_kwargs or {}
                self.tokenizer = types.SimpleNamespace(pad_token=None, eos_token="<|endoftext|>", pad_token_id=151643)
                self.model = types.SimpleNamespace(config=types.SimpleNamespace(pad_token_id=None))
                created_instances.append(self)

        fake_st = types.ModuleType("sentence_transformers")
        fake_st.CrossEncoder = FakeCrossEncoder

        _CROSS_ENCODER_CACHE.clear()
        with unittest.mock.patch.dict(sys.modules, {"torch": fake_torch, "sentence_transformers": fake_st}):
            ce1 = get_cached_cross_encoder("mixedbread-ai/mxbai-rerank-large-v2", max_length=1024, device="cuda")
            ce2 = get_cached_cross_encoder("mixedbread-ai/mxbai-rerank-large-v2", max_length=1024, device="cuda")

        self.assertIs(ce1, ce2)
        self.assertEqual(len(created_instances), 1)
        self.assertEqual(ce1.tokenizer.pad_token, "<|endoftext|>")
        self.assertEqual(ce1.model.config.pad_token_id, 151643)
        self.assertEqual(ce1.model_kwargs.get("dtype"), "float16")
        _CROSS_ENCODER_CACHE.clear()

    def test_rerank_contextual_passage_forwarding_and_top_k_pruning(self):
        """Verify NexusClient.search(rerank=True, rerank_top_k=1) passes contextual passages and prunes output."""
        seen_passages = []

        def spy_reranker(query, texts):
            seen_passages.extend(texts)
            # Score municipal_code highest when contextual Document header is present
            return [0.95 if "Document: municipal code" in t else 0.12 for t in texts]

        hits = self.nexus.search(
            query="lease notice deposit",
            workspace="RetrievalTest",
            limit=5,
            rerank=True,
            rerank_fn=spy_reranker,
            rerank_candidates=6,
            rerank_top_k=1,
        )
        self.assertEqual(len(hits), 1)
        self.assertGreater(len(seen_passages), 0)
        self.assertTrue(any(p.startswith("Document: ") for p in seen_passages))
        self.assertEqual(hits[0].filename, "municipal_code.txt")
        self.assertIn("cross_encoder_rerank", hits[0].match_reasons)
        # Ensure returned SearchHit.text remains exact raw chunk text (no Document: prefix mutation)
        self.assertFalse(hits[0].text.startswith("Document: "))

    def test_legal_cartridge_optional_rerank_contract(self):
        """Verify LegalAuthorityCartridge.search supports optional rerank_fn while keeping rerank=False unchanged."""
        import sqlite3
        import struct
        from krusch_nexus import LegalAuthorityCartridge

        cart_path = os.path.join(self.temp_dir, "mini_authority.sqlite")
        conn = sqlite3.connect(cart_path)
        conn.executescript(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                title TEXT,
                jurisdiction TEXT,
                court TEXT,
                decision_year INTEGER,
                source_table TEXT
            );
            CREATE TABLE chunks (
                chunk_id TEXT PRIMARY KEY,
                doc_id INTEGER,
                chunk_index INTEGER,
                citation TEXT,
                heading_path TEXT,
                locator TEXT,
                text TEXT,
                source_hash TEXT,
                char_start INTEGER,
                char_end INTEGER,
                embedding BLOB
            );
            CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, title, citation, locator, text);
            """
        )
        vec_a = struct.pack("4f", 1.0, 0.0, 0.0, 0.0)
        vec_b = struct.pack("4f", 0.7, 0.7, 0.0, 0.0)
        conn.execute("INSERT INTO documents VALUES (1, 'Alpha Corp NDA', 'DE', 'Contract', 2025, 'contracts')")
        conn.execute("INSERT INTO documents VALUES (2, 'Beta Holdings NDA', 'NY', 'Contract', 2025, 'contracts')")
        conn.execute(
            "INSERT INTO chunks VALUES ('chk_0', 1, 0, 'Alpha p.1', '[\"Section 1\"]', 'Section 1', 'Confidentiality survival term is two years.', 'h1', 0, 43, ?)",
            (vec_a,),
        )
        conn.execute(
            "INSERT INTO chunks VALUES ('chk_1', 2, 0, 'Beta p.1', '[\"Section 9\"]', 'Section 9', 'Confidentiality survival term is five years.', 'h2', 0, 44, ?)",
            (vec_b,),
        )
        conn.execute("INSERT INTO chunks_fts VALUES ('chk_0', 'Alpha Corp NDA', 'Alpha p.1', 'Section 1', 'Confidentiality survival term is two years.')")
        conn.execute("INSERT INTO chunks_fts VALUES ('chk_1', 'Beta Corp NDA', 'Beta p.1', 'Section 9', 'Confidentiality survival term is five years.')")
        conn.commit()
        conn.close()

        cart = LegalAuthorityCartridge(
            db_path=cart_path,
            embed_fn=lambda _q: [1.0, 0.0, 0.0, 0.0],
        )
        stock_hits = cart.search("Confidentiality survival term", limit=2, rerank=False)
        self.assertEqual(stock_hits[0]["chunk_id"], "chk_0")

        reranked_hits = cart.search(
            "Beta Holdings confidentiality survival term",
            limit=1,
            rerank=True,
            rerank_fn=lambda _q, texts: [0.99 if "Beta Holdings NDA" in t else 0.05 for t in texts],
        )
        self.assertEqual(len(reranked_hits), 1)
        self.assertEqual(reranked_hits[0]["chunk_id"], "chk_1")
        self.assertAlmostEqual(reranked_hits[0]["rerank_score"], 0.99, places=4)

    def test_airgap_rejects_cloud_reranker_backend_and_model(self):
        """Verify NexusConfig rejects cloud rerankers when allow_cloud=False."""
        from krusch_nexus.exceptions import AirGapViolationError

        with self.assertRaises(AirGapViolationError):
            NexusConfig(reranker_backend="cohere", allow_cloud=False)

        with self.assertRaises(AirGapViolationError):
            NexusConfig(
                reranker_backend="sentence_transformers",
                reranker_model="rerank-english-v3.0",
                allow_cloud=False,
            )


