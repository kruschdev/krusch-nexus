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
from typing import Optional
try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    class FastMCP:  # type: ignore
        """Fallback FastMCP stub when mcp library is absent from environment."""
        def __init__(self, name: str = "KruschNexusMCP", *args, **kwargs):
            self.name = name
            self._tools = {}
            self._tool_manager = type("_ToolManager", (), {"_tools": self._tools})()

        def tool(self, *args, **kwargs):
            def decorator(f):
                self._tools[f.__name__] = f
                return f
            return decorator

        def run(self, *args, **kwargs):
            raise RuntimeError(
                "The 'mcp' package is required to run the FastMCP server. "
                "Install it with `pip install mcp` or run within mcp_env."
            )

from .client import NexusClient
from .models import DocType

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
def nexus_reparse(
    document_id: int,
    confirmation_token: Optional[str] = None,
    operator_confirmed: bool = False,
    operator_token: Optional[str] = None
) -> str:
    """
    Re-parse and re-chunk an existing document in the corpus.
    OPERATOR ACTION: Requires typed confirmation_token='CONFIRM_REPARSE_<id>'.
    
    Args:
        document_id: Database ID of the document to re-parse.
        confirmation_token: Typed confirmation token matching 'CONFIRM_REPARSE_<document_id>'.
        operator_confirmed: Confirmation flag. Must be accompanied by confirmation_token.
        operator_token: Optional operator token if configured.
    """
    expected = f"CONFIRM_REPARSE_{document_id}"
    if confirmation_token != expected:
        return json.dumps({
            "status": "error",
            "error": f"nexus_reparse is an operator-only action. You must supply confirmation_token='{expected}' to proceed."
        })

    op_token = operator_token or os.getenv("NEXUS_OPERATOR_TOKEN")
    try:
        report = get_client().reparse(
            document_id,
            operator_token=op_token,
            confirmation_token=confirmation_token
        )
        return json.dumps(report.model_dump(), indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_delete_document(
    document_id: int,
    confirmation_token: Optional[str] = None,
    operator_confirmed: bool = False,
    operator_token: Optional[str] = None
) -> str:
    """
    Delete a document and all associated chunks from the corpus.
    OPERATOR ACTION: Requires typed confirmation_token='CONFIRM_DELETE_<id>'.
    
    Args:
        document_id: Database ID of the document to delete.
        confirmation_token: Typed confirmation token matching 'CONFIRM_DELETE_<document_id>'.
        operator_confirmed: Optional legacy confirmation flag. Must be accompanied by confirmation_token.
        operator_token: Optional operator token if configured.
    """
    expected = f"CONFIRM_DELETE_{document_id}"
    if confirmation_token != expected:
        return json.dumps({
            "status": "error",
            "error": f"nexus_delete_document is a destructive operator action. You must supply confirmation_token='{expected}' to proceed."
        })

    op_token = operator_token or os.getenv("NEXUS_OPERATOR_TOKEN")
    try:
        success = get_client().delete_document(
            document_id,
            operator_token=op_token,
            confirmation_token=confirmation_token
        )
        if success:
            return json.dumps({"status": "deleted", "document_id": document_id})
        return json.dumps({"status": "not_found", "message": f"Document ID {document_id} not found"})
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_export_workspace(workspace: str, output_path: Optional[str] = None, token: Optional[str] = None) -> str:
    """
    Export a complete workspace as a standalone .tar.gz archive.
    
    Args:
        workspace: Name of the workspace to export.
        output_path: Optional destination filepath.
        token: Optional API token for workspace authorization.
    """
    if not workspace or not workspace.strip():
        return json.dumps({"status": "error", "error": "workspace is required."})
    ws = workspace.strip()
    err = verify_workspace_access(ws, token)
    if err:
        return json.dumps({"status": "error", "error": err})

    try:
        archive_path = get_client().export_workspace(workspace=ws, output_path=output_path)
        return json.dumps({
            "status": "success",
            "workspace": ws,
            "archive_path": archive_path
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_import_workspace(tarball_path: str, target_workspace: Optional[str] = None, token: Optional[str] = None) -> str:
    """
    Import a workspace archive (.tar.gz) into the local database and archival store.
    
    Args:
        tarball_path: Absolute or relative path to the workspace .tar.gz archive.
        target_workspace: Optional override name for the imported workspace.
        token: Optional API token for workspace authorization.
    """
    if target_workspace:
        err = verify_workspace_access(target_workspace, token)
        if err:
            return json.dumps({"status": "error", "error": err})

    try:
        res = get_client().import_workspace(tarball_path=tarball_path, target_workspace=target_workspace)
        return json.dumps(res, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_set_legal_hold(
    workspace_name: str,
    legal_hold: bool = True,
    operator_token: Optional[str] = None
) -> str:
    """
    Place or release a legal hold on a workspace.
    When a workspace is under active legal hold, document deletion, reparsing, and workspace purging are strictly blocked.
    
    Args:
        workspace_name: Name of the workspace.
        legal_hold: True to activate legal hold, False to release.
        operator_token: Optional operator token if configured.
    """
    if not workspace_name or not workspace_name.strip():
        return json.dumps({"status": "error", "error": "workspace_name is required."})

    op_token = operator_token or os.getenv("NEXUS_OPERATOR_TOKEN")
    try:
        res = get_client().set_legal_hold(
            workspace=workspace_name.strip(),
            legal_hold=legal_hold,
            operator_token=op_token
        )
        return json.dumps(res, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_export_legal_hold_bundle(
    workspace_name: str,
    output_path: Optional[str] = None
) -> str:
    """
    Generate a cryptographic, tamper-evident Legal Hold bundle for a workspace.
    Includes full document manifests, SHA-256 content hashes, chunk locators, and operator audit records.
    
    Args:
        workspace_name: Name of the workspace.
        output_path: Optional destination filepath to write bundle JSON.
    """
    if not workspace_name or not workspace_name.strip():
        return json.dumps({"status": "error", "error": "workspace_name is required."})

    try:
        bundle = get_client().export_legal_hold_bundle(
            workspace=workspace_name.strip(),
            output_path=output_path
        )
        return json.dumps(bundle, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_purge_workspace(
    workspace_name: str,
    confirmation_token: Optional[str] = None,
    operator_token: Optional[str] = None
) -> str:
    """
    Purge an entire workspace and all its documents, chunks, and ingest runs.
    OPERATOR ACTION: Requires typed confirmation_token='CONFIRM_PURGE_<workspace_name>'.
    Strictly blocked if workspace is under active legal hold.
    
    Args:
        workspace_name: Name of the workspace to purge.
        confirmation_token: Typed confirmation token matching 'CONFIRM_PURGE_<workspace_name>'.
        operator_token: Optional operator token if configured.
    """
    if not workspace_name or not workspace_name.strip():
        return json.dumps({"status": "error", "error": "workspace_name is required."})

    ws = workspace_name.strip()
    expected = f"CONFIRM_PURGE_{ws}"
    if confirmation_token != expected:
        return json.dumps({
            "status": "error",
            "error": f"nexus_purge_workspace is a destructive operator action. You must supply confirmation_token='{expected}' to proceed."
        })

    op_token = operator_token or os.getenv("NEXUS_OPERATOR_TOKEN")
    try:
        success = get_client().purge_workspace(
            workspace=ws,
            confirmation_token=confirmation_token,
            operator_token=op_token
        )
        if success:
            return json.dumps({"status": "purged", "workspace": ws})
        return json.dumps({"status": "not_found", "message": f"Workspace '{ws}' not found"})
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
