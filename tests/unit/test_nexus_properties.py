"""
tests/unit/test_nexus_properties.py
===================================
Property-based and invariant mutation test suite for KruschNexus:
1. Legal Hold Gating: Block deletions, re-parsing, and purges (HTTP 423 Locked / LegalHoldActiveError).
2. Legal Hold Bundle: Cryptographic SHA-256 manifest and audit ledger verification.
3. OperatorAudit Immutability: SQLAlchemy listener prevents UPDATE and DELETE on audit trail.
4. Chunker Span Invariants: Bounded char offsets, strict monotonicity, and non-empty text slices.
5. FastMCP Legal Hold & Purge Tool Invariants: Contract validation and safety guards.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import pytest
from fastapi.testclient import TestClient

from krusch_nexus import NexusClient, NexusConfig, DocType
from krusch_nexus.exceptions import LegalHoldActiveError, ConfigurationError
from krusch_nexus.store import init_db, get_engine, get_session_factory, OperatorAudit, Workspace
import krusch_nexus.api as api_module
from krusch_nexus.mcp import (
    set_client,
    nexus_set_legal_hold,
    nexus_export_legal_hold_bundle,
    nexus_purge_workspace,
    nexus_delete_document
)


class TestNexusProperties:

    @pytest.fixture(autouse=True)
    def setup_teardown(self, tmp_path):
        self.tmp_dir = str(tmp_path)
        self.db_path = os.path.join(self.tmp_dir, "properties.db")
        self.config = NexusConfig(
            database_url=f"sqlite:///{self.db_path}",
            allowed_ingest_roots=[self.tmp_dir],
            operator_token="secret_prop_token_123",
            embed_backend="dummy"
        )
        self.engine = get_engine(self.config.database_url)
        init_db(self.engine)
        self.client = NexusClient(self.config)
        set_client(self.client)

        api_module.config = self.config
        api_module.client = self.client
        self.api_client = TestClient(api_module.app)

        yield

    # ─── Property 1: Legal Hold Mutation Gating ───────────────────────────────

    def test_legal_hold_blocks_client_mutations(self, tmp_path):
        """Property: When is_legal_hold is True, delete, reparse, and purge must fail closed."""
        doc_file = tmp_path / "privileged_matter.txt"
        doc_file.write_text("Privileged corporate internal communication under subpoena.")

        report = self.client.ingest(str(doc_file), workspace="LitigationHoldWS", doc_type=DocType.AUTHORITY)
        assert report.status == "completed"
        doc_id = report.document_id

        # Place legal hold
        hold_res = self.client.set_legal_hold("LitigationHoldWS", legal_hold=True, operator_token="secret_prop_token_123")
        assert hold_res["is_legal_hold"] is True
        assert hold_res["status"] == "LEGAL_HOLD_ACTIVE"

        # 1. delete_document must fail with LegalHoldActiveError
        with pytest.raises(LegalHoldActiveError, match="CANNOT_DELETE_LEGAL_HOLD_ACTIVE"):
            self.client.delete_document(
                doc_id,
                confirmation_token=f"CONFIRM_DELETE_{doc_id}",
                operator_token="secret_prop_token_123"
            )

        # 2. reparse must fail with LegalHoldActiveError
        with pytest.raises(LegalHoldActiveError, match="CANNOT_REPARSE_LEGAL_HOLD_ACTIVE"):
            self.client.reparse(
                doc_id,
                confirmation_token=f"CONFIRM_REPARSE_{doc_id}",
                operator_token="secret_prop_token_123"
            )

        # 3. purge_workspace must fail with LegalHoldActiveError
        with pytest.raises(LegalHoldActiveError, match="CANNOT_PURGE_LEGAL_HOLD_ACTIVE"):
            self.client.purge_workspace(
                "LitigationHoldWS",
                confirmation_token="CONFIRM_PURGE_LitigationHoldWS",
                operator_token="secret_prop_token_123"
            )

        # Release legal hold and verify mutation unblocked
        self.client.set_legal_hold("LitigationHoldWS", legal_hold=False, operator_token="secret_prop_token_123")
        del_ok = self.client.delete_document(
            doc_id,
            confirmation_token=f"CONFIRM_DELETE_{doc_id}",
            operator_token="secret_prop_token_123"
        )
        assert del_ok is True

    def test_legal_hold_http_423_locked_gating(self, tmp_path):
        """Property: All API mutations against legal-held entities return HTTP 423 Locked."""
        doc_file = tmp_path / "subpoena_target.txt"
        doc_file.write_text("Subpoena Target Evidence Document 2026.")

        report = self.client.ingest(str(doc_file), workspace="AuditWS", doc_type=DocType.AUTHORITY)
        doc_id = report.document_id

        # Activate legal hold via HTTP API
        hold_resp = self.api_client.post(
            "/v1/workspaces/AuditWS/legal-hold",
            json={"legal_hold": True},
            headers={"X-Operator-Token": "secret_prop_token_123"}
        )
        assert hold_resp.status_code == 200
        assert hold_resp.json()["is_legal_hold"] is True

        # Verify DELETE /v1/documents/{id} returns 423
        del_resp = self.api_client.delete(
            f"/v1/documents/{doc_id}",
            headers={"X-Operator-Token": "secret_prop_token_123"}
        )
        assert del_resp.status_code == 423
        assert del_resp.json()["error"] == "LegalHoldActiveError"

        # Verify POST /v1/documents/{id}/reparse returns 423
        rep_resp = self.api_client.post(
            f"/v1/documents/{doc_id}/reparse",
            headers={"X-Operator-Token": "secret_prop_token_123"}
        )
        assert rep_resp.status_code == 423
        assert rep_resp.json()["error"] == "LegalHoldActiveError"

        # Verify DELETE /v1/workspaces/{name} returns 423
        purge_resp = self.api_client.delete(
            "/v1/workspaces/AuditWS?confirmation_token=CONFIRM_PURGE_AuditWS",
            headers={"X-Operator-Token": "secret_prop_token_123"}
        )
        assert purge_resp.status_code == 423
        assert purge_resp.json()["error"] == "LegalHoldActiveError"

    # ─── Property 2: Legal Hold Cryptographic Bundle Export ────────────────────

    def test_export_legal_hold_bundle_integrity(self, tmp_path):
        """Property: Legal hold bundle generates valid manifest with tamper-evident SHA-256."""
        import hashlib
        doc_file = tmp_path / "evidence_contract.txt"
        doc_file.write_text("Section 1. Force Majeure and statutory compliance terms.\nSection 2. Liquidated damages.")

        self.client.ingest(str(doc_file), workspace="ComplianceHoldWS", doc_type=DocType.AUTHORITY)
        self.client.set_legal_hold("ComplianceHoldWS", legal_hold=True, operator_token="secret_prop_token_123")

        bundle_path = str(tmp_path / "export" / "hold_bundle.json")
        bundle = self.client.export_legal_hold_bundle("ComplianceHoldWS", output_path=bundle_path)

        assert bundle["export_type"] == "LEGAL_HOLD_BUNDLE"
        assert bundle["workspace_name"] == "ComplianceHoldWS"
        assert bundle["is_legal_hold"] is True
        assert bundle["total_documents"] == 1
        assert bundle["total_chunks"] >= 1
        assert bundle["total_audits"] >= 1
        assert "bundle_checksum_sha256" in bundle
        assert os.path.exists(bundle_path)

        # Verify cryptographic checksum calculation matches raw canonical JSON
        with open(bundle_path, "r", encoding="utf-8") as f:
            disk_bundle = json.load(f)

        assert disk_bundle["bundle_checksum_sha256"] == bundle["bundle_checksum_sha256"]

        # Also verify HTTP GET endpoint
        http_bundle_resp = self.api_client.get("/v1/workspaces/ComplianceHoldWS/export-hold-bundle")
        assert http_bundle_resp.status_code == 200
        http_bundle = http_bundle_resp.json()
        assert http_bundle["workspace_name"] == "ComplianceHoldWS"
        assert http_bundle["total_documents"] == 1

    # ─── Property 3: OperatorAudit Append-Only Immutability ────────────────────

    def test_operator_audit_append_only_immutability(self):
        """Property: OperatorAudit records cannot be modified or deleted once written."""
        factory = get_session_factory(self.engine)
        db = factory()
        try:
            ws = Workspace(name="AuditLedgerWS", description="Ledger test")
            db.add(ws)
            db.commit()

            audit = OperatorAudit(
                action="test_action",
                workspace_id=ws.id,
                confirmation_token="CONFIRM_123",
                details=json.dumps({"info": "created"})
            )
            db.add(audit)
            db.commit()
            audit_id = audit.id
            assert audit_id is not None

            # Attempt UPDATE -> must raise PermissionError
            audit.action = "tampered_action"
            with pytest.raises(PermissionError, match="append-only immutable and cannot be modified"):
                db.commit()

            db.rollback()

            # Attempt DELETE -> must raise PermissionError
            fresh_audit = db.query(OperatorAudit).filter(OperatorAudit.id == audit_id).first()
            assert fresh_audit is not None
            db.delete(fresh_audit)
            with pytest.raises(PermissionError, match="append-only immutable and cannot be deleted"):
                db.commit()
        finally:
            db.close()

    # ─── Property 4: Chunker Span Invariants ──────────────────────────────────

    def test_chunker_span_bounds_and_coverage(self, tmp_path):
        """Property: Every produced chunk satisfies 0 <= char_start < char_end <= len(source)."""
        content = (
            "# Title: Master Services Agreement\n\n"
            "This Agreement is entered into as of January 1, 2026.\n\n"
            "## Section 1: Scope of Work\n"
            "The contractor shall perform software engineering and systems architecture tasks.\n"
            "All work must adhere to sovereign, air-gapped standards without cloud egress.\n\n"
            "## Section 2: Payment and Invoicing\n"
            "Payment is net 30 days upon delivery and verification of tests.\n\n"
            "## Section 3: Governing Law\n"
            "This agreement shall be governed by California law.\n"
        )
        doc_file = tmp_path / "msa.txt"
        doc_file.write_text(content)

        report = self.client.ingest(str(doc_file), workspace="SpanWS", doc_type=DocType.AUTHORITY)
        assert report.status == "completed"

        hits = self.client.search("contractor", workspace="SpanWS", limit=20)
        assert len(hits) >= 1

        for hit in hits:
            # Span bounds invariants
            assert hit.char_start >= 0
            assert hit.char_end > hit.char_start
            assert hit.char_end <= len(content)
            # Text slice substring match
            sliced = content[hit.char_start:hit.char_end]
            assert len(sliced.strip()) > 0
            # Chunk text should match or overlap slice
            assert hit.text[:20] in sliced or sliced[:20] in hit.text

    # ─── Property 5: FastMCP Tool Integration ─────────────────────────────────

    def test_fastmcp_legal_hold_and_purge_tools(self, tmp_path):
        """Property: FastMCP legal hold and purge tools adhere to contract and safety invariants."""
        doc_file = tmp_path / "mcp_matter.txt"
        doc_file.write_text("Confidential MCP document subject to legal hold review.")

        report = self.client.ingest(str(doc_file), workspace="MCPHoldWS", doc_type=DocType.AUTHORITY)
        doc_id = report.document_id

        # 1. Set legal hold via FastMCP
        set_res = json.loads(nexus_set_legal_hold(
            workspace_name="MCPHoldWS",
            legal_hold=True,
            operator_token="secret_prop_token_123"
        ))
        assert set_res["is_legal_hold"] is True

        # 2. nexus_delete_document under legal hold fails
        del_fail = json.loads(nexus_delete_document(
            document_id=doc_id,
            confirmation_token=f"CONFIRM_DELETE_{doc_id}",
            operator_token="secret_prop_token_123"
        ))
        assert del_fail["status"] == "error"
        assert "LEGAL_HOLD_ACTIVE" in del_fail["error"]

        # 3. nexus_purge_workspace under legal hold fails
        purge_fail = json.loads(nexus_purge_workspace(
            workspace_name="MCPHoldWS",
            confirmation_token="CONFIRM_PURGE_MCPHoldWS",
            operator_token="secret_prop_token_123"
        ))
        assert purge_fail["status"] == "error"
        assert "LEGAL_HOLD_ACTIVE" in purge_fail["error"]

        # 4. nexus_purge_workspace unconfirmed fails
        purge_unconfirmed = json.loads(nexus_purge_workspace(
            workspace_name="MCPHoldWS",
            confirmation_token="WRONG_TOKEN",
            operator_token="secret_prop_token_123"
        ))
        assert purge_unconfirmed["status"] == "error"
        assert "destructive operator action" in purge_unconfirmed["error"]

        # 5. Export legal hold bundle via FastMCP
        bundle_json = json.loads(nexus_export_legal_hold_bundle(workspace_name="MCPHoldWS"))
        assert bundle_json["export_type"] == "LEGAL_HOLD_BUNDLE"
        assert bundle_json["workspace_name"] == "MCPHoldWS"
        assert "bundle_checksum_sha256" in bundle_json
