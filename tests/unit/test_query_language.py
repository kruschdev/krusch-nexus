"""
tests/unit/test_query_language.py
=================================
Unit tests for query language operators:
- Negation operator: -term excludes matching chunks
- doc_type: operator sets active doc_type filter
- page: operator sets target page number
- header: operator sets target header pattern
- Debug modes: lexical_only / fts_only and vector_only
- Fails closed on zero match
"""

import os
import shutil
import tempfile
import unittest
from krusch_nexus import NexusClient, NexusConfig, DocType
from krusch_nexus.store import init_db, get_engine


class TestQueryLanguage(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nexus_query_lang_")
        self.db_path = os.path.join(self.temp_dir, "test_ql.db")
        self.config = NexusConfig(
            database_url=f"sqlite:///{self.db_path}",
            allowed_ingest_roots=[self.temp_dir]
        )
        self.engine = get_engine(self.config.database_url)
        init_db(self.engine)
        self.client = NexusClient(self.config)

        # Ingest test documents
        self.doc1 = os.path.join(self.temp_dir, "contract_v1.txt")
        with open(self.doc1, "w", encoding="utf-8") as f:
            f.write(
                "Section 1.1 Scope of Work\n"
                "The vendor shall provide electrical maintenance services according to schedule.\n\n"
                "Section 1.2 Confidential Information\n"
                "All proprietary schemas shall remain strictly confidential and privileged.\n\n"
                "Section 1.3 Draft Notes\n"
                "This draft proposal is subject to further executive revision."
            )

        self.doc2 = os.path.join(self.temp_dir, "statute_memo.txt")
        with open(self.doc2, "w", encoding="utf-8") as f:
            f.write(
                "Section 8.22 Permitted Use of Premises\n"
                "Tenant may use the leased premises exclusively for commercial storage.\n\n"
                "Section 8.23 Default and Termination\n"
                "Failure to pay monthly base rent constitutes immediate default."
            )

        self.client.ingest(self.doc1, workspace="LegalWorkspace", doc_type=DocType.WORK_PRODUCT)
        self.client.ingest(self.doc2, workspace="LegalWorkspace", doc_type=DocType.AUTHORITY)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_negative_term_operator(self):
        """Assert -draft excludes chunks containing 'draft'."""
        # Query without negation hits Section 1.3
        hits_all = self.client.search("revision proposal", workspace="LegalWorkspace")
        self.assertTrue(any("draft" in h.text.lower() for h in hits_all))

        # Query with -draft excludes the chunk
        hits_filtered = self.client.search("revision proposal -draft", workspace="LegalWorkspace")
        for h in hits_filtered:
            self.assertNotIn("draft", h.text.lower())

    def test_doctype_operator(self):
        """Assert doc_type:authority restricts results to authority documents."""
        hits = self.client.search("Section doc_type:authority", workspace="LegalWorkspace")
        self.assertGreater(len(hits), 0)
        for h in hits:
            self.assertEqual(h.doc_type, "authority")
            self.assertEqual(h.filename, "statute_memo.txt")

    def test_page_operator(self):
        """Assert page:1 restricts results to page 1."""
        hits = self.client.search("electrical maintenance page:1", workspace="LegalWorkspace")
        self.assertGreater(len(hits), 0)
        for h in hits:
            self.assertEqual(h.page_number, 1)

    def test_header_operator(self):
        """Assert header:Permitted filters by header regex."""
        hits = self.client.search("premises header:Permitted", workspace="LegalWorkspace")
        self.assertGreater(len(hits), 0)
        for h in hits:
            self.assertIn("Permitted", h.header or "")

    def test_lexical_only_mode(self):
        """Assert lexical_only mode retrieves without dense embedding."""
        hits = self.client.search("proprietary schemas", workspace="LegalWorkspace", mode="lexical_only")
        self.assertGreater(len(hits), 0)
        top = hits[0]
        self.assertIn("proprietary schemas", top.text.lower())

    def test_vector_only_mode(self):
        """Assert vector_only mode functions properly."""
        hits = self.client.search("electrical maintenance", workspace="LegalWorkspace", mode="vector_only")
        self.assertGreater(len(hits), 0)

    def test_fail_closed_zero_match(self):
        """Assert impossible query returns empty list without hallucination."""
        hits = self.client.search("xyzzy12345quuxnotfoundinanycorpus", workspace="LegalWorkspace")
        self.assertEqual(len(hits), 0)


if __name__ == "__main__":
    unittest.main()
