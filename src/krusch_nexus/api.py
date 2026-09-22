"""
KruschNexus REST API (HTTP Twin)
================================
First-class HTTP API mirror of the in-process Nexus SDK:
- POST /v1/ingest: Direct multipart file upload or local filepath ingestion
- POST /v1/search: Hybrid vector + FTS retrieval with canonical citations
- GET /v1/documents: List documents with workspace isolation
- GET /v1/documents/{id}/report: Fetch stored IngestReport
- GET /v1/workspaces: List workspaces
- GET /health: Air-gapped healthcheck verifying DB, pgvector, and Ollama
"""

import os
import shutil
import tempfile
import httpx
from typing import Optional, List
from pydantic import BaseModel, Field
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from contextlib import asynccontextmanager

from .config import NexusConfig
from .models import IngestReport, ChunkHit, WorkspaceInfo, DocumentInfo
from .client import Nexus
from .db import init_db, Workspace


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="KruschNexus API",
    version="1.0.0",
    description="Universal Offline Document Ingestion Engine & Page-True Citation Spine",
    lifespan=lifespan
)

config = NexusConfig.from_env()
client = Nexus(config)


# ─── Healthcheck Endpoint ─────────────────────────────────────────────────────

@app.get("/health")
def health_check():
    """
    Verifies connectivity to the database, pgvector extension,
    and the local Ollama embedding host.
    """
    engine = client.engine
    db_ok = False
    vector_ok = False
    ollama_ok = False
    errors = []

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            db_ok = True
            if engine.dialect.name == "postgresql":
                res = conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).fetchone()
                vector_ok = bool(res)
            else:
                vector_ok = True
    except Exception as e:
        errors.append(f"Database connection error: {e}")

    try:
        resp = httpx.get(f"{config.ollama_url}/api/version", timeout=3.0)
        ollama_ok = resp.status_code == 200
    except Exception as e:
        errors.append(f"Ollama host unreachable at {config.ollama_url}: {e}")

    all_healthy = db_ok and vector_ok and ollama_ok
    status_code = status.HTTP_200_OK if all_healthy else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "healthy" if all_healthy else "degraded",
            "database": "connected" if db_ok else "disconnected",
            "pgvector_extension": "active" if vector_ok else "missing",
            "ollama": "connected" if ollama_ok else "unreachable",
            "embed_model": config.embed_model,
            "errors": errors
        }
    )


# ─── Ingestion Endpoints ──────────────────────────────────────────────────────

@app.post("/v1/ingest", response_model=IngestReport)
async def ingest_document(
    file: Optional[UploadFile] = File(None),
    filepath: Optional[str] = Form(None),
    workspace: str = Form(...),  # Required
    doc_type: str = Form("general"),
    archive: bool = Form(False)
):
    """
    Ingest a document into a workspace. Supports either multipart file upload
    or a validated local filesystem path.
    """
    if not workspace or not workspace.strip():
        raise HTTPException(status_code=400, detail="A workspace name is required.")

    if file:
        temp_dir = tempfile.mkdtemp(prefix="nexus_upload_")
        target_path = os.path.join(temp_dir, file.filename)
        try:
            with open(target_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            report = client.ingest(
                filepath=target_path,
                workspace=workspace.strip(),
                doc_type=doc_type,
                archive=False
            )
            return report
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    elif filepath:
        if not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail=f"File '{filepath}' not found on server")
        return client.ingest(
            filepath=filepath,
            workspace=workspace.strip(),
            doc_type=doc_type,
            archive=archive
        )

    raise HTTPException(
        status_code=400,
        detail="Either a multipart 'file' or a local 'filepath' must be provided"
    )


# ─── Search Endpoints ─────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    workspace: str = Field(..., description="Target workspace name (required)")
    doc_type: Optional[str] = None
    limit: int = Field(default=5, ge=1, le=50)


@app.post("/v1/search", response_model=List[ChunkHit])
def search_corpus(req: SearchRequest):
    """
    Execute hybrid vector + full-text search across a workspace.
    Returns ranked chunks with canonical citations.
    """
    if not req.workspace or not req.workspace.strip():
        raise HTTPException(status_code=400, detail="Search requires a specific workspace.")

    hits = client.search(
        query=req.query,
        workspace=req.workspace.strip(),
        doc_type=req.doc_type,
        limit=req.limit
    )
    return hits


# ─── Document & Workspace Metadata Endpoints ──────────────────────────────────

@app.get("/v1/documents", response_model=List[DocumentInfo])
def list_documents(workspace: Optional[str] = Query(None)):
    """List all ingested documents with optional workspace filtering."""
    return client.list_documents(workspace=workspace)


@app.get("/v1/documents/{doc_id_or_hash}/report", response_model=IngestReport)
def get_document_ingest_report(doc_id_or_hash: str):
    """Fetch the stored IngestReport for a document."""
    report = client.get_ingest_report(doc_id_or_hash)
    if not report:
        raise HTTPException(status_code=404, detail=f"Document '{doc_id_or_hash}' not found")
    return report


@app.get("/v1/workspaces", response_model=List[WorkspaceInfo])
def list_workspaces():
    """List all workspaces and their indexed document counts."""
    return client.list_workspaces()


class CreateWorkspaceRequest(BaseModel):
    name: str
    description: Optional[str] = None


@app.post("/v1/workspaces", response_model=WorkspaceInfo)
def create_workspace(req: CreateWorkspaceRequest):
    """Create a new isolated document workspace."""
    db = client._get_db()
    try:
        existing = db.query(Workspace).filter(Workspace.name == req.name).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Workspace '{req.name}' already exists")

        ws = Workspace(name=req.name, description=req.description)
        db.add(ws)
        db.commit()
        db.refresh(ws)
        return WorkspaceInfo(
            id=ws.id,
            name=ws.name,
            description=ws.description,
            document_count=0,
            created_at=str(ws.created_at)
        )
    finally:
        db.close()
