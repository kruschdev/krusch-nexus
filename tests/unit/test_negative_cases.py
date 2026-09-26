"""
Unit tests for KruschNexus Negative Cases, Invariants, and Security Boundaries.
Tests:
1. Encrypted PDF rejection (EncryptedPdfError)
2. 0-byte empty file rejection
3. Unsupported MIME extension rejection (UnsupportedMimeError)
4. HTML script stripping invariant
5. Breadcrumb separation invariant (breadcrumbs never concatenated into embed text or chunk hash)
6. Cross-workspace zero-leakage invariant with identical text payloads
"""

import os
import shutil
import tempfile
import unittest

from krusch_nexus import (
    NexusClient,
    NexusConfig,
    DocType,
    EncryptedPdfError
)
from krusch_nexus.parsers import parse_html, parse_pdf
from krusch_nexus.chunking import chunk_document_pages
from krusch_nexus.models import PageData
from krusch_nexus.store import init_db, get_engine

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")


class TestNegativeCasesAndInvariants(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nexus_neg_test_")
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.config = NexusConfig(
            database_url=f"sqlite:///{self.db_path}",
            allowed_ingest_roots=[FIXTURES_DIR, self.temp_dir]
        )
        self.engine = get_engine(self.config.database_url)
        init_db(self.engine)
        self.client = NexusClient(self.config)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_encrypted_pdf_rejection(self):
        """Verify encrypted PDF raises EncryptedPdfError and is never indexed."""
        enc_src = os.path.join(FIXTURES_DIR, "encrypted_sample.pdf")
        with self.assertRaises(EncryptedPdfError):
            parse_pdf(enc_src, "encrypted_sample.pdf")

        enc_path = os.path.join(self.temp_dir, "encrypted_sample.pdf")
        shutil.copy2(enc_src, enc_path)
        report = self.client.ingest(
            filepath=enc_path,
            workspace="Matter_Security",
            doc_type=DocType.AUTHORITY
        )
        self.assertEqual(report.status, "failed")
        self.assertIn("EncryptedPdfError", report.error)

    def test_zero_byte_empty_file_rejection(self):
        """Verify 0-byte empty file is rejected with ParseError without crashing."""
        empty_path = os.path.join(self.temp_dir, "empty_file.txt")
        with open(empty_path, "w") as f:
            pass
        report = self.client.ingest(
            filepath=empty_path,
            workspace="Matter_Empty",
            doc_type=DocType.GENERAL
        )
        self.assertEqual(report.status, "failed")
        self.assertIn("empty", report.error.lower())

    def test_unsupported_extension_rejection(self):
        """Verify unsupported file extension raises UnsupportedMimeError."""
        invalid_path = os.path.join(self.temp_dir, "malicious_payload.exe")
        with open(invalid_path, "wb") as f:
            f.write(b"MZ\x90\x00malicious")
        report = self.client.ingest(
            filepath=invalid_path,
            workspace="Matter_Security",
            doc_type=DocType.GENERAL
        )
        self.assertEqual(report.status, "failed")
        self.assertIn("UnsupportedMimeError", report.error)

    def test_html_scripts_stripped(self):
        """Verify HTML parser completely removes script and style contents."""
        html_path = os.path.join(FIXTURES_DIR, "script_injection.html")
        result = parse_html(html_path, "script_injection.html")
        full_text = result.full_text

        self.assertNotIn("alert('pwned')", full_text)
        self.assertNotIn("document.cookie", full_text)
        self.assertNotIn("console.log", full_text)
        self.assertIn("Corporate Governance", full_text)
        self.assertIn("Legitimate content here", full_text)

    def test_breadcrumb_separation_invariant(self):
        """
        CRITICAL INVARIANT TEST:
        Breadcrumbs must NEVER be concatenated into embed text or chunk hashes.
        Assert that hashes and embed text are identical whether or not metadata contains breadcrumbs,
        and differ ONLY when the raw text payload changes.
        """
        raw_text = "The tenant shall maintain comprehensive general liability insurance in the amount of $2,000,000."

        page_with_header = PageData(
            index=1,
            locator="Article IX > Section 9.1",
            text=f"# Section 9.1: Insurance Obligations\n\n{raw_text}"
        )
        chunks_1 = chunk_document_pages([page_with_header], filename="lease_a.pdf", file_hash="hash_a")

        page_different_filename = PageData(
            index=14,
            locator="Exhibit C > Section 9.1",
            text=f"# Section 9.1: Insurance Obligations\n\n{raw_text}"
        )
        chunks_2 = chunk_document_pages([page_different_filename], filename="amendment_b.pdf", file_hash="hash_b")

        # Citations are different
        self.assertNotEqual(chunks_1[0].citation, chunks_2[0].citation)
        self.assertIn("lease_a.pdf", chunks_1[0].citation)
        self.assertIn("amendment_b.pdf", chunks_2[0].citation)

        # But text to embed and source_hash MUST BE IDENTICAL because the raw text is identical!
        self.assertEqual(chunks_1[0].text, chunks_2[0].text)
        self.assertEqual(chunks_1[0].source_hash, chunks_2[0].source_hash)

        # Changing raw text payload MUST change hash
        different_raw = raw_text + " Deductibles shall not exceed $10,000."
        page_altered = PageData(
            index=1,
            locator="Article IX > Section 9.1",
            text=f"# Section 9.1: Insurance Obligations\n\n{different_raw}"
        )
        chunks_altered = chunk_document_pages([page_altered], filename="lease_a.pdf", file_hash="hash_a")
        self.assertNotEqual(chunks_1[0].source_hash, chunks_altered[0].source_hash)

    def test_cross_workspace_isolation_identical_text(self):
        """
        CRITICAL SECURITY INVARIANT:
        Two workspaces contain identical document text.
        Querying Workspace Alpha must return ONLY chunks belonging to Workspace Alpha.
        Zero cross-workspace leakage.
        """
        sample_path = os.path.join(FIXTURES_DIR, "municipal_code.txt")

        # Ingest into Workspace_Red
        rep_red = self.client.ingest(sample_path, workspace="Workspace_Red", doc_type=DocType.AUTHORITY)
        self.assertEqual(rep_red.status, "completed")

        # Ingest into Workspace_Blue
        rep_blue = self.client.ingest(sample_path, workspace="Workspace_Blue", doc_type=DocType.AUTHORITY)
        self.assertEqual(rep_blue.status, "completed")

        # Search Red
        hits_red = self.client.search("§ 1950.5 security deposit rent", workspace="Workspace_Red", limit=10)
        self.assertGreater(len(hits_red), 0)
        for h in hits_red:
            self.assertEqual(h.workspace, "Workspace_Red", "Chunk from Workspace_Blue leaked into Workspace_Red search!")

        # Search Blue
        hits_blue = self.client.search("Section 8.22.030 rent adjustment", workspace="Workspace_Blue", limit=10)
        self.assertGreater(len(hits_blue), 0)
        for h in hits_blue:
            self.assertEqual(h.workspace, "Workspace_Blue", "Chunk from Workspace_Red leaked into Workspace_Blue search!")


if __name__ == "__main__":
    unittest.main()
