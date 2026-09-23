"""
KruschNexus FastMCP Server (mcp.py)
===================================
Exposes local-first document ingestion and hybrid retrieval tools to AI agents.
Enforces strict workspace requirements, structured citations, and operator-gated
destructive operations.
"""

import os
import json
import logging
from typing import Optional, List
from mcp.server.fastmcp import FastMCP

from .client import NexusClient
from .models import DocType, NexusConfig

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


# ─── Workspace ACL Verification ───────────────────────────────────────────────

def verify_workspace_access(workspace: str, token: Optional[str] = None) -> Optional[str]:
    """
    Verify whether the token is authorized to access the given workspace.
    Returns None if authorized, or an error description string if access is denied.
    """
    client = get_client()
    token_map = getattr(client.config, "token_workspaces", {})
    if not token_map:
        return None

    supplied = token or os.getenv("NEXUS_API_TOKEN")
    if not supplied:
        return "Authentication required: no API token provided for workspace access."

    if supplied in (client.config.api_token, client.config.operator_token):
        return None

    allowed = token_map.get(supplied)
    if allowed is None:
        return "Access denied: invalid API token."

    if "*" not in allowed and workspace not in allowed:
        return f"Access denied: token not authorized for workspace '{workspace}'."

    return None


# ─── 6 Canonical User Tools ──────────────────────────────────────────────────

@mcp.tool()
def nexus_list_workspaces(token: Optional[str] = None) -> str:
    """List document workspaces and indexed document counts, filtered by token authorization."""
    try:
        client = get_client()
        token_map = getattr(client.config, "token_workspaces", {})
        workspaces = client.list_workspaces()

        if token_map:
            supplied = token or os.getenv("NEXUS_API_TOKEN")
            if not supplied:
                return json.dumps({"status": "error", "error": "Authentication required: no API token provided"})
            if supplied not in (client.config.api_token, client.config.operator_token):
                allowed = token_map.get(supplied)
                if allowed is None:
                    return json.dumps({"status": "error", "error": "Access denied: invalid API token"})
                if "*" not in allowed:
                    workspaces = [w for w in workspaces if w.name in allowed]

        results = [w.model_dump() for w in workspaces]
        return json.dumps({"workspaces": results, "count": len(results)}, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_list_documents(workspace_name: Optional[str] = None, token: Optional[str] = None) -> str:
    """List ingested documents in a specific workspace or across authorized workspaces."""
    try:
        if workspace_name:
            err = verify_workspace_access(workspace_name.strip(), token)
            if err:
                return json.dumps({"status": "error", "error": err})

        docs = get_client().list_documents(workspace=workspace_name)
        client = get_client()
        token_map = getattr(client.config, "token_workspaces", {})
        if token_map and not workspace_name:
            supplied = token or os.getenv("NEXUS_API_TOKEN")
            if not supplied:
                return json.dumps({"status": "error", "error": "Authentication required: no API token provided"})
            if supplied not in (client.config.api_token, client.config.operator_token):
                allowed = set(token_map.get(supplied, []))
                if "*" not in allowed:
                    docs = [d for d in docs if d.workspace in allowed]

        results = [d.model_dump() for d in docs]
        return json.dumps({"documents": results, "count": len(results)}, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_ingest_file(
    file_path: str,
    workspace_name: str,
    doc_type: str = "general",
    archive: bool = False,
    token: Optional[str] = None
) -> str:
    """
    Ingest a local document into the KruschNexus corpus.
    
    Args:
        file_path: Path to document file (must reside within approved ingest roots).
        workspace_name: Target workspace name (REQUIRED - no cross-contamination defaults).
        doc_type: Classification category ('authority', 'work_product', 'fact_narrative', 'general').
        archive: Whether to move source file to .ingested/ upon successful indexing.
        token: Optional API token for workspace authorization when ACLs are configured.
    """
    if not workspace_name or not workspace_name.strip():
        return json.dumps({
            "status": "error",
            "error": "workspace_name is required. Cross-contamination defaults are disallowed."
        })

    err = verify_workspace_access(workspace_name.strip(), token)
    if err:
        return json.dumps({"status": "error", "error": err})

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
def nexus_ingest_directory(
    directory_path: str,
    workspace_name: str,
    doc_type: str = "general",
    recursive: bool = False,
    token: Optional[str] = None
) -> str:
    """
    Ingest all supported documents from a directory into a workspace.
    
    Args:
        directory_path: Local directory path. Must be within approved ingest roots.
        workspace_name: Target workspace name (REQUIRED).
        doc_type: Classification category.
        recursive: Whether to scan subdirectories recursively.
        token: Optional API token for workspace authorization when ACLs are configured.
    """
    if not workspace_name or not workspace_name.strip():
        return json.dumps({"status": "error", "error": "workspace_name is required."})

    err = verify_workspace_access(workspace_name.strip(), token)
    if err:
        return json.dumps({"status": "error", "error": err})

    client = get_client()
    reports = []
    from .ingest import ALLOWED_EXT

    try:
        resolved_doc_type = DocType(doc_type.lower()) if doc_type.lower() in [e.value for e in DocType] else DocType.GENERAL
        walker = os.walk(directory_path) if recursive else [(directory_path, [], os.listdir(directory_path))]

        for root, _, files in walker:
            for f in sorted(files):
                if f.startswith(".") or f.endswith(".part"):
                    continue
                if f.lower().endswith(ALLOWED_EXT):
                    f_path = os.path.join(root, f)
                    rep = client.ingest(filepath=f_path, workspace=workspace_name.strip(), doc_type=resolved_doc_type)
                    reports.append(rep.model_dump())

        return json.dumps({
            "status": "completed",
            "workspace": workspace_name,
            "total_ingested": len(reports),
            "reports": reports
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
    limit: int = 5,
    page: Optional[int] = None,
    doc_id: Optional[int] = None,
    filename: Optional[str] = None,
    token: Optional[str] = None
) -> str:
    """
    Execute hybrid vector + full-text search across a specific workspace.
    Returns structured citation fields with grounded text snippets.
    
    Args:
        query: Search question, keywords, or statutory section tokens (e.g. '§ 1950.5' or '"liquidated damages"').
        workspace_name: Target workspace name (REQUIRED).
        doc_type: Optional document type filter.
        limit: Number of top chunk hits to return (default 5).
        page: Optional physical page number predicate (exact SQL filter).
        doc_id: Optional document ID predicate (exact SQL filter).
        filename: Optional source filename predicate (exact SQL filter).
        token: Optional API token for workspace authorization when ACLs are configured.
    """
    if not workspace_name or not workspace_name.strip():
        return json.dumps({
            "status": "error",
            "error": "workspace_name is required. Global multi-workspace search is disallowed."
        })

    err = verify_workspace_access(workspace_name.strip(), token)
    if err:
        return json.dumps({"status": "error", "error": err})

    try:
        ws = workspace_name.strip()
        search_filters = {}
        if page is not None:
            search_filters["page"] = page
        if doc_id is not None:
            search_filters["doc_id"] = doc_id
        if filename is not None:
            search_filters["filename"] = filename

        hits = get_client().search(
            query=query,
            workspace=ws,
            doc_type=doc_type,
            limit=limit,
            filters=search_filters if search_filters else None
        )
        results = []
        for h in hits:
            results.append({
                "chunk_id": h.chunk_id,
                "document_id": h.document_id,
                "filename": h.filename,
                "workspace": h.workspace,
                "score": h.score,
                "text": h.text,
                "citation": {
                    "filename": h.filename,
                    "page_number": h.page_number,
                    "header": h.header,
                    "locator": h.locator,
                    "formatted": h.citation,
                    "structured_locator": h.structured_locator.model_dump() if h.structured_locator else None,
                    "heading_path": h.heading_path
                },
                "explainability": {
                    "dense_score": h.dense_score,
                    "sparse_score": h.sparse_score,
                    "vector_rank": h.vector_rank,
                    "fts_rank": h.fts_rank,
                    "section_boost": h.section_boost,
                    "phrase_boost": h.phrase_boost,
                    "match_reasons": h.match_reasons
                }
            })

        return json.dumps({
            "status": "success",
            "query": query,
            "workspace": ws,
            "results_count": len(results),
            "results": results
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


# ─── Operator-Restricted Tools ───────────────────────────────────────────────

@mcp.tool()
def nexus_reparse(document_id: int, operator_confirmed: bool = False, operator_token: Optional[str] = None) -> str:
    """
    Re-parse and re-chunk an existing document in the corpus.
    OPERATOR ACTION: Requires operator_confirmed=True.
    
    Args:
        document_id: Database ID of the document to re-parse.
        operator_confirmed: Confirmation flag. Must be set to True.
        operator_token: Optional operator token if configured.
    """
    if not operator_confirmed:
        return json.dumps({
            "status": "error",
            "error": "nexus_reparse is an operator-only action. You must pass operator_confirmed=True to proceed."
        })

    try:
        report = get_client().reparse(document_id, operator_token=operator_token)
        return json.dumps(report.model_dump(), indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_delete_document(document_id: int, operator_confirmed: bool = False, operator_token: Optional[str] = None) -> str:
    """
    Delete a document and all associated chunks from the corpus.
    OPERATOR ACTION: Requires operator_confirmed=True.
    
    Args:
        document_id: Database ID of the document to delete.
        operator_confirmed: Confirmation flag. Must be set to True.
        operator_token: Optional operator token if configured.
    """
    if not operator_confirmed:
        return json.dumps({
            "status": "error",
            "error": "nexus_delete_document is a destructive operator action. You must pass operator_confirmed=True to proceed."
        })

    try:
        success = get_client().delete_document(document_id, operator_token=operator_token)
        if success:
            return json.dumps({"status": "deleted", "document_id": document_id})
        return json.dumps({"status": "not_found", "message": f"Document ID {document_id} not found"})
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
