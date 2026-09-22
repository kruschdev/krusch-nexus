"""
KruschNexus FastMCP Server
==========================
Exposes local-first document ingestion and hybrid retrieval tools to AI agents.
Enforces strict workspace requirements, path sandboxing, and localhost-only binding.
"""

import os
import json
import logging
from typing import Optional
from mcp.server.fastmcp import FastMCP

from .client import Nexus
from .config import NexusConfig
from .exceptions import PathSandboxError, WorkspaceRequiredError

logger = logging.getLogger("krusch_nexus.mcp")

mcp = FastMCP("KruschNexusMCP")
_client: Optional[Nexus] = None


def set_client(client: Nexus):
    global _client
    _client = client


def get_client() -> Nexus:
    global _client
    if _client is None:
        _client = Nexus.from_env()
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
            "error": "workspace_name is required. Defaulting to general workspaces is disallowed."
        })

    try:
        report = get_client().ingest(
            filepath=file_path,
            workspace=workspace_name.strip(),
            doc_type=doc_type,
            archive=archive
        )
        return json.dumps(report.model_dump(), indent=2)
    except Exception as e:
        logger.error(f"MCP ingestion failed: {type(e).__name__}: {e}")
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_ingest_directory(
    directory_path: str,
    workspace_name: str,
    archive: bool = False,
    recursive: bool = False
) -> str:
    """
    Batch-ingest all supported documents from a local directory into a workspace.
    
    Args:
        directory_path: Path to local directory. Must reside within approved ingest roots.
        workspace_name: Target workspace name (REQUIRED).
        archive: Whether to archive processed files.
        recursive: Whether to scan subdirectories.
    """
    if not workspace_name or not workspace_name.strip():
        return json.dumps({
            "status": "error",
            "error": "workspace_name is required."
        })

    try:
        reports = get_client().ingest_directory(
            dirpath=directory_path,
            workspace=workspace_name.strip(),
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

    # Security check: warn if host is bound outside 127.0.0.1 without authorization
    if host not in ("127.0.0.1", "localhost", "0.0.0.0"):
        logger.warning(f"Binding MCP server to non-local address {host}")

    if transport == "sse":
        print(f"Starting KruschNexus MCP Server in SSE mode on {host}:{port}...", flush=True)
        mcp.run(transport="sse", host=host, port=port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
