"""
KruschNexus FastMCP Server
==========================
Exposes local-first document ingestion, OCR extraction, structural chunking,
and hybrid vector/FTS search tools to AI agents via FastMCP (Stdio & SSE).
"""

import os
import json
import logging
from typing import Optional, List
import httpx
from mcp.server.fastmcp import FastMCP
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger("krusch_nexus.mcp")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://krusch:kruschpassword@localhost:5432/krusch_nexus_db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

OLLAMA_EMBED_HOST = os.getenv("OLLAMA_EMBED_HOST", os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "bge-large")

# Initialize FastMCP
mcp = FastMCP("KruschNexusMCP")

# Database connection
connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_embedding(query: str) -> Optional[List[float]]:
    """Generate embedding for query via local Ollama /api/embed."""
    try:
        resp = httpx.post(
            f"{OLLAMA_EMBED_HOST}/api/embed",
            json={"model": EMBED_MODEL, "input": query},
            timeout=30.0
        )
        resp.raise_for_status()
        embeddings = resp.json().get("embeddings", [[]])
        if embeddings and embeddings[0]:
            return embeddings[0]
    except Exception as e:
        logger.warning(f"Ollama batch embed failed: {e}. Trying legacy /api/embeddings...")
        try:
            resp = httpx.post(
                f"{OLLAMA_EMBED_HOST}/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": query},
                timeout=30.0
            )
            resp.raise_for_status()
            emb = resp.json().get("embedding", [])
            if emb:
                return emb
        except Exception as e2:
            logger.error(f"Local embedding failed: {e2}")
    return None


@mcp.tool()
def nexus_list_workspaces() -> str:
    """
    List all document workspaces (e.g. General, Legal, Corporate, Research).
    """
    try:
        from src.backend.db import SessionLocal as NexusSessionLocal, Workspace
        db = NexusSessionLocal()
        try:
            workspaces = db.query(Workspace).all()
            results = [{"id": w.id, "name": w.name, "description": w.description} for w in workspaces]
            return json.dumps({"workspaces": results, "count": len(results)}, indent=2)
        finally:
            db.close()
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_list_documents(workspace_name_or_id: Optional[str] = None, limit: int = 20) -> str:
    """
    List ingested documents in a specific workspace or across all workspaces.
    """
    try:
        from src.backend.db import SessionLocal as NexusSessionLocal, Workspace, Document as DocModel
        db = NexusSessionLocal()
        try:
            query = db.query(DocModel)
            if workspace_name_or_id:
                if str(workspace_name_or_id).isdigit():
                    query = query.filter(DocModel.workspace_id == int(workspace_name_or_id))
                else:
                    ws = db.query(Workspace).filter(Workspace.name.ilike(workspace_name_or_id)).first()
                    if ws:
                        query = query.filter(DocModel.workspace_id == ws.id)

            docs = query.order_by(DocModel.id.desc()).limit(limit).all()
            results = [{
                "id": d.id,
                "filename": d.filename,
                "workspace_id": d.workspace_id,
                "file_hash": d.file_hash,
                "total_pages": d.total_pages,
                "total_chunks": d.total_chunks,
                "doc_type": d.doc_type,
                "uploaded_at": str(getattr(d, "uploaded_at", ""))
            } for d in docs]
            return json.dumps({"documents": results, "count": len(results)}, indent=2)
        finally:
            db.close()
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
        from src.backend.client import NexusIngestClient
        client = NexusIngestClient()
        report = client.ingest_file(file_path, workspace=workspace_name, doc_type=doc_type, archive=archive)
        return json.dumps(report, indent=2)
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
        from src.backend.client import NexusIngestClient
        client = NexusIngestClient()
        reports = client.ingest_directory(directory_path, workspace=workspace_name, archive=archive, recursive=recursive)
        return json.dumps({
            "status": "completed",
            "directory": directory_path,
            "workspace": workspace_name,
            "total_files_processed": len(reports),
            "reports": reports
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
        from src.backend.client import NexusIngestClient
        client = NexusIngestClient()
        report = client.get_ingest_report(doc_id_or_hash)
        if report:
            return json.dumps(report, indent=2)
        return json.dumps({"status": "not_found", "message": f"Document '{doc_id_or_hash}' not found"})
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_search_corpus(
    query: str,
    workspace_name: Optional[str] = None,
    doc_type: Optional[str] = None,
    limit: int = 5
) -> str:
    """
    Execute hybrid vector (HNSW cosine) + full-text (tsvector) Reciprocal Rank Fusion (RRF) search
    across all ingested document chunks. Returns exact page/section citations [filename, p. X, § Section]
    and grounded text snippets.
    """
    try:
        from src.backend.client import NexusIngestClient
        client = NexusIngestClient()
        chunks = client.search_corpus(query=query, workspace=workspace_name, doc_type=doc_type, limit=limit)
        return json.dumps({
            "status": "success",
            "query": query,
            "results_count": len(chunks),
            "results": chunks
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def nexus_classify_document(doc_id: int, classification_level: str, allowed_roles: str = "all") -> str:
    """
    Set security classification level ('public', 'internal', 'confidential', 'management_only')
    and allowed role access for a document.
    """
    try:
        from src.backend.db import SessionLocal as NexusSessionLocal, Document
        db = NexusSessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if not doc:
                return json.dumps({"error": f"Document ID {doc_id} not found"})

            doc.classification_level = classification_level.lower()
            doc.allowed_roles = allowed_roles
            db.commit()
            return json.dumps({
                "status": "success",
                "doc_id": doc.id,
                "filename": doc.filename,
                "classification_level": doc.classification_level,
                "allowed_roles": doc.allowed_roles
            })
        finally:
            db.close()
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def nexus_flag_document_for_review(doc_id: int, reason: str) -> str:
    """
    Flag a document as sensitive or suspicious for review and audit.
    """
    try:
        from src.backend.db import SessionLocal as NexusSessionLocal, Document
        db = NexusSessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if not doc:
                return json.dumps({"error": f"Document ID {doc_id} not found"})

            doc.flagged_for_review = True
            doc.flag_reason = reason
            db.commit()
            return json.dumps({
                "status": "success",
                "doc_id": doc.id,
                "filename": doc.filename,
                "flagged_for_review": True,
                "flag_reason": reason
            })
        finally:
            db.close()
    except Exception as e:
        return json.dumps({"error": str(e)})


def main():
    """Console script entrypoint for nexus-mcp."""
    transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8002"))

    if transport == "sse":
        print(f"Starting KruschNexus MCP Server in SSE mode on {host}:{port}...", flush=True)
        mcp.run(transport="sse", host=host, port=port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
