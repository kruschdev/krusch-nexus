"""
tests/test_invariants.py
========================
Automated pass/fail test suite verifying the 10 Core Architectural Invariants
of KruschNexus as specified in docs/INVARIANTS.md.
"""

from __future__ import annotations

import pytest

from krusch_nexus import NexusClient, NexusConfig, DocType
from krusch_nexus.exceptions import AirGapViolationError, UnsupportedMimeError, AuthenticationError
from krusch_nexus.ingest.sandbox import validate_file_magic_bytes
from krusch_nexus.store import UniversalVector


class TestKruschNexusInvariants:
    """Pass/Fail test matrix for INV-1 through INV-10."""

    def test_inv_01_air_gap_zero_cloud_egress(self):
        """INV-1: Absolute Air-Gap. Refuse cloud embedding providers or endpoints."""
        # Cloud provider rejected
        with pytest.raises(AirGapViolationError, match="Insecure embedding provider"):
            NexusConfig(embedding_provider="openai", allow_cloud=False)

        # Cloud endpoint rejected
        with pytest.raises(AirGapViolationError, match="Air-gap violation"):
            NexusConfig(ollama_url="https://api.openai.com/v1", allow_cloud=False)

        # Cloud model rejected
        with pytest.raises(AirGapViolationError, match="Air-gap violation"):
            NexusConfig(embed_model="text-embedding-3-small", allow_cloud=False)

    def test_inv_02_page_faithful_citation(self, tmp_path):
        """INV-2: Page-Faithful Citation. Chunk locators preserve page numbers."""
        cfg = NexusConfig(embed_backend="dummy", database_url=f"sqlite:///{tmp_path}/paged.db")
        client = NexusClient(config=cfg)

        doc_file = tmp_path / "contract_paged.txt"
        doc_file.write_text("Page 1 content.\n\nSection 1.0\nText on first page.")

        report = client.ingest(str(doc_file), workspace="PageWS", doc_type=DocType.AUTHORITY)
        assert report.status == "completed"

        hits = client.search("first page", workspace="PageWS")
        assert len(hits) >= 1
        assert hits[0].page_number == 1
        assert "p.1" in hits[0].citation

    def test_inv_03_structure_first_chunking(self, tmp_path):
        """INV-3: Structure-First Chunking. Subsection headers and paths are preserved."""
        cfg = NexusConfig(embed_backend="dummy", database_url=f"sqlite:///{tmp_path}/struct.db")
        client = NexusClient(config=cfg)

        doc_file = tmp_path / "bylaws.txt"
        doc_file.write_text("ARTICLE II: BOARD OF DIRECTORS\nSection 2.1 Quorum\nA majority of directors constitutes quorum.")

        report = client.ingest(str(doc_file), workspace="StructWS", doc_type=DocType.AUTHORITY)
        assert report.status == "completed"

        hits = client.search("quorum", workspace="StructWS")
        assert len(hits) >= 1
        assert hits[0].header in ("Section 2.1 Quorum", "ARTICLE II: BOARD OF DIRECTORS")
        assert "Quorum" in (hits[0].header or "") or any("ARTICLE" in p for p in hits[0].heading_path)

    def test_inv_04_strict_workspace_isolation(self, tmp_path):
        """INV-4: Strict Multi-Tenant / Workspace Isolation. Zero cross-workspace leakage."""
        cfg = NexusConfig(embed_backend="dummy", database_url=f"sqlite:///{tmp_path}/iso.db")
        client = NexusClient(config=cfg)

        doc_a = tmp_path / "secret_alpha.txt"
        doc_a.write_text("CLASSIFIED_TOKEN_ALPHA_9988 Top secret merger terms.")
        client.ingest(str(doc_a), workspace="AlphaTenant")

        # Search in Alpha succeeds
        hits_alpha = client.search("CLASSIFIED_TOKEN_ALPHA_9988", workspace="AlphaTenant")
        assert len(hits_alpha) >= 1

        # Search in Beta yields ZERO hits
        hits_beta = client.search("CLASSIFIED_TOKEN_ALPHA_9988", workspace="BetaTenant")
        assert len(hits_beta) == 0

    def test_inv_05_strict_loopback_residency(self):
        """INV-5: Strict Loopback Data Residency. Default API host is 127.0.0.1."""
        cfg = NexusConfig()
        assert cfg.api_host in ("127.0.0.1", "localhost", "::1")

    def test_inv_06_pre_spool_magic_bytes_verification(self, tmp_path):
        """INV-6: Pre-Spool MIME Magic-Byte Verification. Executable binaries rejected."""
        # Executable PE file disguised as PDF
        pe_file = tmp_path / "malicious.pdf"
        pe_file.write_bytes(b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 100)

        with pytest.raises(UnsupportedMimeError, match="Windows PE executable binary detected"):
            validate_file_magic_bytes(pe_file)

        # Linux ELF binary disguised as TXT
        elf_file = tmp_path / "script.txt"
        elf_file.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 100)

        with pytest.raises(UnsupportedMimeError, match="Linux ELF executable binary detected"):
            validate_file_magic_bytes(elf_file)

        # HTML disguised as PDF
        html_pdf = tmp_path / "fake.pdf"
        html_pdf.write_bytes(b"<html><head><script>alert(1)</script></head><body>Phishing</body></html>")

        with pytest.raises(UnsupportedMimeError, match="Script/HTML masquerading as PDF"):
            validate_file_magic_bytes(html_pdf)

    def test_inv_07_idempotent_hashing_deduplication(self, tmp_path):
        """INV-7: Idempotent Hashing & Content Deduplication."""
        cfg = NexusConfig(embed_backend="dummy", database_url=f"sqlite:///{tmp_path}/idemp.db")
        client = NexusClient(config=cfg)

        doc = tmp_path / "lease.txt"
        doc.write_text("Lease agreement term: Rent is $3000/mo.")

        rep1 = client.ingest(str(doc), workspace="IdempWS")
        assert rep1.status == "completed"

        # Re-ingest same document
        rep2 = client.ingest(str(doc), workspace="IdempWS")
        assert rep2.status in ("completed", "skipped_duplicate")
        assert rep2.file_hash == rep1.file_hash
        assert rep2.document_id == rep1.document_id

    def test_inv_08_universal_vector_dual_engine(self):
        """INV-8: Deterministic Dual-Engine Vector Support. UniversalVector functions on SQLite."""
        uv = UniversalVector(dim=1024)
        assert uv.dim == 1024

        # Test bind param serialization on SQLite
        class MockDialect:
            name = "sqlite"

        bound = uv.process_bind_param([0.1, 0.2, 0.3], MockDialect())
        assert isinstance(bound, str)
        assert "[0.1, 0.2, 0.3]" in bound

        # Test result deserialization
        result = uv.process_result_value(bound, MockDialect())
        assert isinstance(result, list)
        assert result == [0.1, 0.2, 0.3]

    def test_inv_09_resilient_state_machine_ledger(self, tmp_path):
        """INV-9: Resilient 8-State Ingest Ledger. Tracks lifecycle transitions."""
        cfg = NexusConfig(embed_backend="dummy", database_url=f"sqlite:///{tmp_path}/state.db")
        client = NexusClient(config=cfg)

        doc = tmp_path / "notes.txt"
        doc.write_text("Meeting notes from deposition.")

        report = client.ingest(str(doc), workspace="StateWS")
        assert report.status == "completed"
        assert report.total_chunks >= 1
        assert report.doc_type == "general"

    def test_inv_10_operator_token_gated_destruction(self, tmp_path):
        """INV-10: Operator Token Gated Destruction."""
        cfg = NexusConfig(
            embed_backend="dummy",
            database_url=f"sqlite:///{tmp_path}/guard.db",
            operator_token="super-secret-operator-token"
        )
        client = NexusClient(config=cfg)

        doc = tmp_path / "purge_target.txt"
        doc.write_text("Document to delete.")
        rep = client.ingest(str(doc), workspace="GuardWS")

        # Deleting without token must fail
        with pytest.raises(AuthenticationError, match="Operator authorization required"):
            client.delete_document(rep.document_id, operator_token=None)

        # Deleting with invalid token must fail
        with pytest.raises(AuthenticationError, match="Operator authorization required"):
            client.delete_document(rep.document_id, operator_token="wrong_token")

        # Deleting with valid token succeeds
        res = client.delete_document(rep.document_id, operator_token="super-secret-operator-token")
        assert res is True
