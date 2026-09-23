"""
tests/unit/test_api_contract.py
===============================
Contract test suite for KruschNexus public API surface.
Enforces:
1. Public symbols strictly adhere to __all__.
2. Canonical client is NexusClient, with Nexus as an identical thin alias.
3. DocType enum integrity (authority, work_product, fact_narrative, general).
4. Frozen Pydantic schemas (SearchHit v1, IngestReport v1) with JSON schema validation.
"""

import pytest
import krusch_nexus


def test_public_api_symbols_in_all():
    """Assert all symbols defined in __all__ are directly exportable and valid."""
    assert hasattr(krusch_nexus, "__all__")
    public_all = krusch_nexus.__all__

    assert "NexusClient" in public_all
    assert "Nexus" in public_all
    assert "DocType" in public_all
    assert "SearchHit" in public_all
    assert "IngestReport" in public_all
    assert "Citation" in public_all
    assert "StructuredLocator" in public_all
    assert "SearchFilter" in public_all
    assert "NexusConfig" in public_all
    assert "IngestState" in public_all
    assert "WarningCode" in public_all
    assert "__version__" in public_all

    for symbol in public_all:
        assert hasattr(krusch_nexus, symbol), f"Symbol '{symbol}' in __all__ but not in package root."


def test_nexus_alias_identity():
    """Assert Nexus is an exact, thin alias of NexusClient."""
    from krusch_nexus import NexusClient, Nexus
    assert Nexus is NexusClient, "Nexus alias must reference NexusClient directly."


def test_doctype_enum_contract():
    """Assert DocType enum values are canonical and cannot drift."""
    from krusch_nexus import DocType

    expected_values = {"authority", "work_product", "fact_narrative", "general"}
    actual_values = {e.value for e in DocType}
    assert actual_values == expected_values, f"DocType values drifted: {actual_values} != {expected_values}"

    assert DocType.AUTHORITY == "authority"
    assert DocType.WORK_PRODUCT == "work_product"
    assert DocType.FACT_NARRATIVE == "fact_narrative"
    assert DocType.GENERAL == "general"


def test_search_hit_schema_contract():
    """Assert SearchHit JSON schema has frozen properties and schema_version."""
    from krusch_nexus import SearchHit

    schema = SearchHit.model_json_schema()
    properties = schema.get("properties", {})

    required_contract_fields = [
        "schema_version",
        "citation",
        "page_number",
        "header",
        "locator",
        "structured_locator",
        "score",
        "text",
        "document_id",
        "chunk_id",
        "phrase_boost",
        "lexical_boost",
        "section_boost",
        "heading_path",
        "match_reasons",
        "vector_rank",
        "fts_rank",
        "doc_type"
    ]

    for field in required_contract_fields:
        assert field in properties, f"SearchHit schema missing required contract property: {field}"

    hit = SearchHit(
        citation="test.pdf p. 2 § Section 1",
        page_number=2,
        header="Section 1",
        locator="Section 1",
        score=0.95,
        text="Sample operative lease term.",
        document_id=1,
        chunk_id=10,
        phrase_boost=True
    )
    assert hit.schema_version == "1.0"
    assert hit.phrase_boost is True
    assert hit.lexical_boost is True  # synchronized alias
    assert hit.structured_locator is not None
    assert hit.structured_locator.page == 2


def test_ingest_report_schema_contract():
    """Assert IngestReport JSON schema has frozen properties and schema_version."""
    from krusch_nexus import IngestReport

    schema = IngestReport.model_json_schema()
    properties = schema.get("properties", {})

    required_contract_fields = [
        "schema_version",
        "status",
        "document_id",
        "filename",
        "workspace",
        "file_hash",
        "doc_type",
        "parser_name",
        "parser_version",
        "tool_versions",
        "detected_mime",
        "total_pages",
        "total_chunks",
        "ocr_pages",
        "ocr_confidence",
        "duration_ms",
        "warnings",
        "error"
    ]

    for field in required_contract_fields:
        assert field in properties, f"IngestReport schema missing required contract property: {field}"

    report = IngestReport(
        filename="test.pdf",
        workspace="litigation",
        file_hash="abc12345",
        total_pages=5,
        total_chunks=12
    )
    assert report.schema_version == "1.0"
    assert report.total_pages == 5
    assert report.pages == 5
    assert report.total_chunks == 12
    assert report.chunks == 12


def test_format_citation_honesty():
    """Assert format_citation never emits 'p. None' and formats unpaged locators honestly."""
    from krusch_nexus import format_citation, Citation, StructuredLocator

    # 1. Paged format (PDF)
    cit_paged = format_citation(filename="lease.pdf", page_number=4, header="Article IV")
    assert "p.4" in cit_paged
    assert "p. None" not in cit_paged
    assert "Article IV" in cit_paged

    # 2. Unpaged heading (DOCX/MD)
    cit_docx = format_citation(filename="policy.docx", locator="1.2 > Backup Retention")
    assert "p. None" not in cit_docx
    assert "p." not in cit_docx
    assert cit_docx == "policy.docx § 1.2 > Backup Retention"

    # 3. Unpaged tabular (CSV)
    cit_csv = format_citation(filename="financials.csv", locator="Rows 1-50")
    assert "p. None" not in cit_csv
    assert cit_csv == "financials.csv Rows 1-50"

    # 4. StructuredLocator structure
    sl = StructuredLocator.from_raw(
        page=None,
        locator_str="1.2 > Backup Retention",
        char_span=(100, 250)
    )
    assert sl.kind == "heading"
    assert sl.page is None
    assert sl.char_span == (100, 250)
    assert "Backup Retention" in sl.path


def test_document_version_lineage(tmp_path):
    """Assert ingesting modified file with same filename creates v2 and supersedes v1."""
    from krusch_nexus import NexusClient, NexusConfig
    from krusch_nexus.store import Document, DocumentChunk

    db_path = str(tmp_path / "lineage_test.db")
    cfg = NexusConfig(database_url=f"sqlite:///{db_path}")
    client = NexusClient(config=cfg)

    f1 = tmp_path / "agreement.txt"
    f1.write_text("First version of operative contract terms.")
    rep1 = client.ingest(filepath=str(f1), workspace="LineageWS")
    assert rep1.status == "completed"

    db = client._get_db()
    d1 = db.query(Document).filter(Document.id == rep1.document_id).first()
    assert d1.version == 1
    assert d1.status == "committed"
    chunks1 = db.query(DocumentChunk).filter(DocumentChunk.document_id == d1.id).all()
    assert all(not c.is_superseded for c in chunks1)

    # Ingest modified file with same filename
    f1.write_text("Second amended version of operative contract terms with new liquidated damages.")
    rep2 = client.ingest(filepath=str(f1), workspace="LineageWS")
    assert rep2.status == "completed"
    assert rep2.document_id != rep1.document_id

    d2 = db.query(Document).filter(Document.id == rep2.document_id).first()
    assert d2.version == 2
    assert d2.status == "committed"

    # Verify prior doc and chunks are superseded
    db.expire_all()
    d1_refreshed = db.query(Document).filter(Document.id == d1.id).first()
    assert d1_refreshed.status == "superseded"
    chunks1_after = db.query(DocumentChunk).filter(DocumentChunk.document_id == d1.id).all()
    assert all(c.is_superseded for c in chunks1_after)
    db.close()


def test_workspace_export_import_roundtrip(tmp_path):
    """Assert workspace can be exported to .tar.gz and cleanly imported into a new workspace."""
    import tarfile
    from krusch_nexus import NexusClient, NexusConfig
    from krusch_nexus.store import Workspace, Document, DocumentChunk

    db_path = str(tmp_path / "export_test.db")
    cfg = NexusConfig(database_url=f"sqlite:///{db_path}")
    client = NexusClient(config=cfg)

    # Ingest two documents
    doc_a = tmp_path / "doc_a.txt"
    doc_a.write_text("First document content for portability test.")
    doc_b = tmp_path / "doc_b.txt"
    doc_b.write_text("Second document content for portability test.")

    client.ingest(str(doc_a), workspace="ExportSource")
    client.ingest(str(doc_b), workspace="ExportSource")

    # Export
    out_archive = str(tmp_path / "export.tar.gz")
    exported_path = client.export_workspace(workspace="ExportSource", output_path=out_archive)
    assert exported_path == out_archive

    # Inspect tarball
    with tarfile.open(exported_path, "r:gz") as tar:
        names = tar.getnames()
        assert "workspace.json" in names
        assert "manifest.json" in names
        assert "chunks.jsonl" in names

    # Import into a new workspace
    res = client.import_workspace(tarball_path=exported_path, target_workspace="ImportTarget")
    assert res["status"] == "success"
    assert res["workspace"] == "ImportTarget"
    assert res["documents_imported"] == 2
    assert res["chunks_imported"] >= 2

    # Verify in DB
    db = client._get_db()
    target_ws = db.query(Workspace).filter(Workspace.name == "ImportTarget").first()
    assert target_ws is not None
    docs = db.query(Document).filter(Document.workspace_id == target_ws.id).all()
    assert len(docs) == 2
    chunks = db.query(DocumentChunk).filter(DocumentChunk.workspace_id == target_ws.id).all()
    assert len(chunks) >= 2
    db.close()

