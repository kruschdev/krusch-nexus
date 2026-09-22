"""
Unit Tests for KruschNexus Hybrid Retrieval & Section Boost
===========================================================
Tests Reciprocal Rank Fusion (RRF), statutory section boosts (§ / Section),
strict workspace isolation, and canonical citation generation.
"""

import os
import shutil
import tempfile
import unittest

from src.backend.config import NexusConfig
from src.backend.db import init_db, get_engine, Workspace, Document, DocumentChunk
from src.backend.rag_engine import hybrid_search, format_citation


class TestHybridSearch(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nexus_search_test_")
        self.db_path = os.path.join(self.temp_dir, "test_search.db")
        self.config = NexusConfig(database_url=f"sqlite:///{self.db_path}")
        self.engine = get_engine(self.config.database_url)
        init_db(self.engine)
        from sqlalchemy.orm import sessionmaker
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

        # Seed 2 isolated workspaces
        self.ws_legal = Workspace(name="Legal", description="Legal authorities")
        self.ws_corp = Workspace(name="Corporate", description="Internal policies")
        self.db.add_all([self.ws_legal, self.ws_corp])
        self.db.commit()

        # Seed documents
        self.doc_legal = Document(
            workspace_id=self.ws_legal.id,
            filename="civil_code.pdf",
            file_hash="hash_legal_1",
            total_pages=15
        )
        self.doc_corp = Document(
            workspace_id=self.ws_corp.id,
            filename="handbook.pdf",
            file_hash="hash_corp_1",
            total_pages=5
        )
        self.db.add_all([self.doc_legal, self.doc_corp])
        self.db.commit()

        # Seed chunks
        # Chunk 1: Civil Code § 1950.5 Security Deposits
        self.chunk_deposit = DocumentChunk(
            document_id=self.doc_legal.id,
            workspace_id=self.ws_legal.id,
            filename="civil_code.pdf",
            page_number=14,
            chunk_index=3,
            header="§ 1950.5 Security Deposits",
            content="A landlord may not demand or receive security in an amount exceeding two months rent.",
            source_hash="s_hash_1",
            doc_hash="hash_legal_1",
            doc_type="authority"
        )
        # Chunk 2: Civil Code General Provisions
        self.chunk_general = DocumentChunk(
            document_id=self.doc_legal.id,
            workspace_id=self.ws_legal.id,
            filename="civil_code.pdf",
            page_number=2,
            chunk_index=0,
            header="General Provisions",
            content="These provisions govern real property and lease relationships within California.",
            source_hash="s_hash_2",
            doc_hash="hash_legal_1",
            doc_type="authority"
        )
        # Chunk 3: Corporate Handbook (different workspace)
        self.chunk_corp = DocumentChunk(
            document_id=self.doc_corp.id,
            workspace_id=self.ws_corp.id,
            filename="handbook.pdf",
            page_number=1,
            chunk_index=0,
            header="Security & Deposits",
            content="Corporate expense policy regarding security deposit advances for remote offices.",
            source_hash="s_hash_3",
            doc_hash="hash_corp_1",
            doc_type="work_product"
        )
        self.db.add_all([self.chunk_deposit, self.chunk_general, self.chunk_corp])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_canonical_citation_formatting(self):
        """Verify format_citation generates canonical string with page and section header."""
        self.assertEqual(
            format_citation("lease.pdf", 14, "§ 8.22"),
            "[lease.pdf, p. 14, § 8.22]"
        )
        self.assertEqual(
            format_citation("memo.docx", 3, "Heading 1"),
            "[memo.docx, p. 3, § Heading 1]"
        )
        self.assertEqual(
            format_citation("summary.txt", 1, None),
            "[summary.txt, p. 1]"
        )

    def test_strict_workspace_isolation(self):
        """Verify queries in workspace 'Legal' never leak chunks from 'Corporate'."""
        hits = hybrid_search(
            query="security deposit",
            workspace_id=self.ws_legal.id,
            db=self.db,
            limit=10
        )
        self.assertGreater(len(hits), 0)
        for h in hits:
            self.assertEqual(h.workspace, "Legal")
            self.assertNotEqual(h.document_id, self.doc_corp.id)
            self.assertNotEqual(h.filename, "handbook.pdf")

    def test_section_heading_boost(self):
        """Verify query mentioning '§ 1950.5' boosts the exact section chunk to rank 1."""
        hits = hybrid_search(
            query="What is the deposit limit under § 1950.5?",
            workspace_id=self.ws_legal.id,
            db=self.db,
            limit=5
        )
        self.assertGreater(len(hits), 0)
        top_hit = hits[0]
        self.assertEqual(top_hit.page_number, 14)
        self.assertIn("§ 1950.5", top_hit.header)
        self.assertEqual(top_hit.citation, "[civil_code.pdf, p. 14, § 1950.5 Security Deposits]")


if __name__ == "__main__":
    unittest.main()
