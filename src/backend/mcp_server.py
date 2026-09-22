"""
KruschNexus FastMCP Server
==========================
Exposes local-first document ingestion, OCR extraction, structural chunking,
and hybrid vector/FTS search tools to AI agents via FastMCP (Stdio & SSE).
Backed directly by the typed NexusIngestClient SDK.
"""

import os
import json
import logging
from typing import Optional, List
from mcp.server.fastmcp import FastMCP

from .client import NexusIngestClient
from .config import NexusConfig

logger = logging.getLogger("krusch_nexus.mcp")

# Initialize FastMCP
mcp = FastMCP("KruschNexusMCP")

# Global client singleton
_client: Optional[NexusIngestClient] = None


def set_client(client: NexusIngestClient):
    global _client
    _client = client


def get_client() -> NexusIngestClient:
    global _client
    if _client is None:
        _client = NexusIngestClient(NexusConfig.from_env())
    return _client


@mcp.tool()
def nexus_list_workspaces() -> str:
    """
    List all document workspaces and their indexed document counts.
    """
    try:
        workspaces = get_client().list_workspaces()
        results = [w.model_dump() for w in workspaces]
        return json.dumps({"workspaces": results, "count": len(results)}, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_list_documents(workspace_name: Optional[str] = None) -> str:
    """
    List ingested documents in a specific workspace or across all workspaces.
    """
    try:
        docs = get_client().list_documents(workspace=workspace_name)
        results = [d.model_dump() for d in docs]
        return json.dumps({"documents": results, "count": len(results)}, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_ingest_file(
    file_path: str,
    workspace_name: str = "General",
    doc_type: str = "general",
    archive: bool = False
) -> str:
    """
    Ingest a local document (PDF with automated local OCR fallback, DOCX, EML, CSV, HTML, TXT/MD)
    into the KruschNexus corpus. Preserves 1-based page numbers, computes SHA-256 hashes,
    generates local embeddings, and stores structural chunks with HNSW & tsvector indexes.
    
    Returns a standardized JSON Ingest Report.
    """
    try:
        report = get_client().ingest_file(
            filepath=file_path,
            workspace=workspace_name,
            doc_type=doc_type,
            archive=archive
        )
        return json.dumps(report.model_dump(), indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_ingest_directory(
    directory_path: str,
    workspace_name: str = "General",
    archive: bool = False,
    recursive: bool = False
) -> str:
    """
    Batch-ingest all supported documents (PDF, DOCX, EML, CSV, HTML, TXT, MD) from a local directory.
    Returns a summary report with per-document ingestion statistics.
    """
    try:
        reports = get_client().ingest_directory(
            dirpath=directory_path,
            workspace=workspace_name,
            archive=archive,
            recursive=recursive
        )
        return json.dumps({
            "status": "completed",
            "directory": directory_path,
            "workspace": workspace_name,
            "total_files_processed": len(reports),
            "reports": [r.model_dump() for r in reports]
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_get_ingest_report(doc_id_or_hash: str) -> str:
    """
    Retrieve the detailed Ingest Report (pages in, chunks out, OCR pages, duration, timestamp)
    for an ingested document by its database ID or SHA-256 hash.
    """
    try:
        report = get_client().get_ingest_report(doc_id_or_hash)
        if report:
            return json.dumps(report.model_dump(), indent=2)
        return json.dumps({"status": "not_found", "message": f"Document '{doc_id_or_hash}' not found"})
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_search_corpus(
    query: str,
    workspace_name: Optional[str] = "General",
    doc_type: Optional[str] = None,
    limit: int = 5
) -> str:
    """
    Execute hybrid vector (HNSW cosine) + full-text (tsvector) Reciprocal Rank Fusion (RRF) search
    across ingested document chunks. Returns exact page/section citations [filename, p. X, § Section]
    and grounded text snippets.
    """
    try:
        ws = workspace_name or "General"
        hits = get_client().search(query=query, workspace=ws, doc_type=doc_type, limit=limit)
        results = [h.model_dump() for h in hits]
        return json.dumps({
            "status": "success",
            "query": query,
            "workspace": ws,
            "results_count": len(results),
            "results": results
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


def main():
    """Console script entrypoint for nexus-mcp."""
    transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8002"))

    if transport == "sse":
        print(f"Starting KruschNexus MCP Server in SSE mode on {host}:{port}...", flush=True)
        mcp.run(transport="sse", host=host, port=port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
