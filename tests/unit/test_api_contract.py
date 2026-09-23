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
