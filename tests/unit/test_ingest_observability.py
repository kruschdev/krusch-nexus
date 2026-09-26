"""
tests/unit/test_ingest_observability.py
=======================================
Verifies operator observability invariants on IngestReport:
  - duration_breakdown_ms records stage-level timings (parse, chunk, embed, db_commit)
  - ocr_trigger_reasons maps page numbers to deterministic trigger reasons
"""

import tempfile
import os
from krusch_nexus import NexusClient, NexusConfig, DocType


def test_ingest_report_observability_breakdown():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f:
        db_path = db_f.name

    cfg = NexusConfig(database_url=f"sqlite:///{db_path}", embed_backend="dummy", embed_dim=1024)
    client = NexusClient(config=cfg)

    with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
        f.write("Section 1.0 Master Services Agreement\nThis is substantive legal prose for testing.\n")
        txt_path = f.name

    try:
        report = client.ingest(txt_path, workspace="ObservabilityWS", doc_type=DocType.WORK_PRODUCT)
        assert report.status == "completed"

        # 1. duration_breakdown_ms invariant
        assert isinstance(report.duration_breakdown_ms, dict)
        assert "parse" in report.duration_breakdown_ms
        assert "chunk" in report.duration_breakdown_ms
        assert "embed" in report.duration_breakdown_ms
        assert "db_commit" in report.duration_breakdown_ms
        assert all(isinstance(v, (int, float)) for v in report.duration_breakdown_ms.values())

        # 2. ocr_trigger_reasons invariant
        assert isinstance(report.ocr_trigger_reasons, dict)
        # TXT files do not need OCR
        for p_idx, reason in report.ocr_trigger_reasons.items():
            assert reason in ("none", "sparse_text", "image_xobject", "forced")
    finally:
        if os.path.exists(txt_path):
            os.remove(txt_path)
        if os.path.exists(db_path):
            os.remove(db_path)


def test_pdf_ocr_trigger_reasons():
    fixture_pdf = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "sample_contract.pdf")
    if not os.path.exists(fixture_pdf):
        return

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f:
        db_path = db_f.name

    try:
        cfg = NexusConfig(database_url=f"sqlite:///{db_path}", embed_backend="dummy", embed_dim=1024)
        client = NexusClient(config=cfg)
        report = client.ingest(fixture_pdf, workspace="PdfObservabilityWS", doc_type=DocType.AUTHORITY)
        assert report.status == "completed"
        assert isinstance(report.ocr_trigger_reasons, dict)
        assert len(report.ocr_trigger_reasons) > 0
        for p_idx, reason in report.ocr_trigger_reasons.items():
            assert reason in ("none", "sparse_text", "image_xobject", "forced")
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)

