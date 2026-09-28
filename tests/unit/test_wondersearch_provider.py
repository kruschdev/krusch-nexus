"""
Unit tests for Wondersearch Cloud Provider in KruschNexus (test_wondersearch_provider.py)
Verifies zero-vendor-lockin pluggability, frozen contract mapping, and airgap preservation.
"""

import os
import json
import pytest
from unittest.mock import patch, MagicMock

from krusch_nexus.models import NexusConfig, SearchHit, IngestReport, DocType
from krusch_nexus.exceptions import AirGapViolationError, ConfigurationError, WorkspaceRequiredError
from krusch_nexus.provider_wondersearch import WondersearchProvider
from krusch_nexus.client import NexusClient


SAMPLE_SEARCH_RESPONSE = {
    "request_id": "00000000-0000-4000-8000-000000000001",
    "drive_id": "00000000-0000-4000-8000-000000000002",
    "results": [
        {
            "document_id": "11111111-1111-4111-8111-111111111111",
            "external_id": "tenant_ordinance_2026.pdf",
            "document_revision": "2",
            "passage_id": "pass-oakland-section-8",
            "row_id": "1",
            "text": "Landlords must provide 60-day notice for rent increases exceeding 5 percent.",
            "start_byte": 120,
            "end_byte": 205,
            "metadata": {
                "filename": "tenant_ordinance_2026.pdf",
                "header": "Section 8.22 Rent Adjustments",
                "doc_type": "authority"
            },
            "score": 0.94
        }
    ],
    "usage": {
        "cost": {
            "amount": "0.002500",
            "currency": "USD"
        }
    },
    "warnings": []
}


class TestWondersearchProvider:
    """Test suite for the Wondersearch cloud retrieval provider."""

    def test_airgap_invariant_rejection(self):
        """INV-1 Check: Wondersearch backend must be blocked if allow_cloud=False."""
        with pytest.raises(AirGapViolationError, match="Cannot use Wondersearch cloud provider with airgap=True"):
            NexusConfig(backend="wondersearch", allow_cloud=False)

    def test_missing_api_key_raises_configuration_error(self):
        """Wondersearch provider requires an explicit API key."""
        cfg = NexusConfig(backend="wondersearch", allow_cloud=True, wondersearch_api_key=None)
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ConfigurationError, match="Wondersearch provider requires an API key"):
                WondersearchProvider(cfg)

    def test_drive_id_resolution(self):
        """Drive IDs should resolve from mapping, direct UUID, or None."""
        cfg = NexusConfig(
            backend="wondersearch",
            allow_cloud=True,
            wondersearch_api_key="ws_key_abc",
            wondersearch_drive_mapping={"Matter_Oakland": "22222222-2222-4222-8222-222222222222"}
        )
        provider = WondersearchProvider(cfg)

        # 1. From drive mapping
        assert provider.resolve_drive_id("Matter_Oakland") == "22222222-2222-4222-8222-222222222222"

        # 2. Direct UUID string
        direct_uuid = "33333333-3333-4333-8333-333333333333"
        assert provider.resolve_drive_id(direct_uuid) == direct_uuid

        # 3. Unknown name without mapping
        assert provider.resolve_drive_id("Unknown_Matter") is None

    @patch("httpx.Client.post")
    def test_search_contract_fidelity(self, mock_post):
        """Verify Wondersearch search translates into frozen SearchHit v1 models."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = SAMPLE_SEARCH_RESPONSE
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        cfg = NexusConfig(
            backend="wondersearch",
            allow_cloud=True,
            wondersearch_api_key="ws_key_abc",
            wondersearch_drive_mapping={"OaklandTenant": "00000000-0000-4000-8000-000000000002"}
        )
        provider = WondersearchProvider(cfg)

        hits = provider.search(
            query="rent increase notice rules",
            workspace="OaklandTenant",
            limit=5
        )

        assert len(hits) == 1
        hit = hits[0]
        assert isinstance(hit, SearchHit)

        # Contract assertions
        assert "tenant_ordinance_2026.pdf" in hit.citation
        assert "Section 8.22" in hit.citation
        assert hit.score == 0.94
        assert hit.dense_score == 0.94
        assert hit.char_start == 120
        assert hit.char_end == 205
        assert hit.text == "Landlords must provide 60-day notice for rent increases exceeding 5 percent."
        assert hit.source_hash == "2"
        assert hit.match_reasons == ["wondersearch_cloud_vector"]
        assert hit.score_vector["cost"]["amount"] == "0.002500"
        assert isinstance(hit.document_id, int)
        assert isinstance(hit.chunk_id, int)
        assert hit.document_id > 0
        assert hit.chunk_id > 0

    @patch("httpx.Client.post")
    def test_ingest_contract_fidelity(self, mock_post, tmp_path):
        """Verify document ingestion returns a valid IngestReport."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "55555555-5555-4555-8555-555555555555",
            "external_id": "test_lease.txt",
            "version": "1"
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        test_file = tmp_path / "test_lease.txt"
        test_file.write_text("Tenant shall pay rent on the first of each calendar month.")

        cfg = NexusConfig(
            backend="wondersearch",
            allow_cloud=True,
            wondersearch_api_key="ws_key_abc",
            wondersearch_drive_mapping={"TenantLeaseWS": "00000000-0000-4000-8000-000000000002"}
        )
        provider = WondersearchProvider(cfg)

        report = provider.ingest(
            filepath=str(test_file),
            workspace="TenantLeaseWS",
            doc_type=DocType.AUTHORITY
        )

        assert isinstance(report, IngestReport)
        assert report.status == "completed"
        assert report.filename == "test_lease.txt"
        assert report.workspace == "TenantLeaseWS"
        assert report.parser_name == "wondersearch_cloud"
        assert report.doc_type == "authority"
        assert len(report.file_hash) == 64

    @patch("httpx.Client.post")
    def test_client_delegation_flow(self, mock_post):
        """Verify NexusClient seamless delegation when backend='wondersearch'."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = SAMPLE_SEARCH_RESPONSE
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        cfg = NexusConfig(
            backend="wondersearch",
            allow_cloud=True,
            wondersearch_api_key="ws_key_abc",
            wondersearch_drive_mapping={"Matter_Smith": "00000000-0000-4000-8000-000000000002"}
        )
        client = NexusClient(config=cfg)

        # Call the public NexusClient.search method
        hits = client.search("60-day notice requirement", workspace="Matter_Smith")

        assert len(hits) == 1
        assert hits[0].filename == "tenant_ordinance_2026.pdf"
        assert hits[0].score == 0.94
        assert hits[0].workspace == "Matter_Smith"
