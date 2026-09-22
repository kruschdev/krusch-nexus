"""
KruschNexus FastMCP Server (mcp.py)
===================================
Exposes local-first document ingestion and hybrid retrieval tools to AI agents.
Enforces strict workspace requirements, path sandboxing, and frozen contract JSON outputs.
"""

import os
import json
import logging
from typing import Optional
from mcp.server.fastmcp import FastMCP

from .client import NexusClient
from .models import DocType
from .config import NexusConfig

logger = logging.getLogger("krusch_nexus.mcp")

mcp = FastMCP("KruschNexusMCP")
_client: Optional[NexusClient] = None


def set_client(client: NexusClient):
    global _client
    _client = client


def get_client() -> NexusClient:
    global _client
    if _client is None:
        _client = NexusClient.from_env()
    return _client


@mcp.tool()
def nexus_list_workspaces() -> str:
    """List all document workspaces and their indexed document counts."""
    try:
        workspaces = get_client().list_workspaces()
        results = [w.model_dump() for w in workspaces]
        return json.dumps({"workspaces": results, "count": len(results)}, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_list_documents(workspace_name: Optional[str] = None) -> str:
    """List ingested documents in a specific workspace or across all workspaces."""
    try:
        docs = get_client().list_documents(workspace=workspace_name)
        results = [d.model_dump() for d in docs]
        return json.dumps({"documents": results, "count": len(results)}, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_ingest_file(
    file_path: str,
    workspace_name: str,
    doc_type: str = "general",
    archive: bool = False
) -> str:
    """
    Ingest a local document into the KruschNexus corpus.
    
    Args:
        file_path: Path to the document file. Must reside within approved ingest roots.
        workspace_name: Target workspace name (REQUIRED - no cross-contamination defaults).
        doc_type: Document classification category ('authority', 'work_product', 'fact_narrative', 'general').
        archive: Whether to move source file to .ingested/ upon successful indexing.
    """
    if not workspace_name or not workspace_name.strip():
        return json.dumps({
            "status": "error",
            "error": "workspace_name is required. Cross-contamination defaults are disallowed."
        })

    try:
        resolved_doc_type = DocType(doc_type.lower()) if doc_type.lower() in [e.value for e in DocType] else DocType.GENERAL
        report = get_client().ingest(
            filepath=file_path,
            workspace=workspace_name.strip(),
            doc_type=resolved_doc_type,
            archive=archive
        )
        return json.dumps(report.model_dump(), indent=2)
    except Exception as e:
        logger.error(f"MCP ingestion failed: {type(e).__name__}: {e}")
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_reparse(document_id: int) -> str:
    """
    Re-parse and re-chunk an existing document in the corpus.
    
    Args:
        document_id: Database ID of the document to re-parse.
    """
    try:
        report = get_client().reparse(document_id)
        return json.dumps(report.model_dump(), indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_delete_document(document_id: int) -> str:
    """
    Delete a document and all associated chunks from the corpus.
    
    Args:
        document_id: Database ID of the document to delete.
    """
    try:
        success = get_client().delete_document(document_id)
        if success:
            return json.dumps({"status": "deleted", "document_id": document_id})
        return json.dumps({"status": "not_found", "message": f"Document ID {document_id} not found"})
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_get_ingest_report(doc_id_or_hash: str) -> str:
    """Retrieve the Ingest Report for a document by its database ID or SHA-256 hash."""
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
    workspace_name: str,
    doc_type: Optional[str] = None,
    limit: int = 5
) -> str:
    """
    Execute hybrid vector + full-text search across a specific workspace.
    
    Args:
        query: Search question or keywords (supports statutory section tokens like § 1950.5).
        workspace_name: Target workspace name (REQUIRED).
        doc_type: Optional document type filter.
        limit: Number of top chunk hits to return (default 5).
    """
    if not workspace_name or not workspace_name.strip():
        return json.dumps({
            "status": "error",
            "error": "workspace_name is required. Global multi-workspace search is disallowed."
        })

    try:
        ws = workspace_name.strip()
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
