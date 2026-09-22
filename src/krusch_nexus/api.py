"""
KruschNexus REST API (api.py)
=============================
First-class HTTP API mirror of the in-process NexusClient SDK:
- POST /v1/ingest: Direct multipart file upload or local filepath ingestion
- POST /v1/search: Hybrid vector + FTS retrieval with canonical citations
- GET /v1/documents: List documents with workspace isolation
- POST /v1/documents/{id}/reparse: Re-parse existing document
- DELETE /v1/documents/{id}: Delete document and cascade chunks
- GET /v1/workspaces: List workspaces
- GET /health: Air-gapped healthcheck verifying DB, pgvector, and Ollama
"""

import os
import shutil
import tempfile
import httpx
from typing import Optional, List
from pydantic import BaseModel, Field
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, Header, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from contextlib import asynccontextmanager

from .config import NexusConfig
from .models import IngestReport, SearchHit, SearchFilter, WorkspaceInfo, DocumentInfo, DocType
from .exceptions import ConfigurationError
from .client import NexusClient
from .store import init_db, Workspace

config = NexusConfig.from_env()
client = NexusClient(config)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Enforce database password security guardrail
    db_url = config.database_url or os.getenv("DATABASE_URL", "")
    insecure_passwords = ["password", "kruschpassword", "admin", "postgres", "root", "123456"]
    for bad_pwd in insecure_passwords:
        if f":{bad_pwd}@" in db_url.lower():
            raise ConfigurationError(
                f"Refusing server boot with insecure database password '{bad_pwd}'. "
                "Set a secure POSTGRES_PASSWORD in your environment or .env file."
            )
    init_db()
    yield


app = FastAPI(
    title="KruschNexus API",
    version="0.2.0",
    description="Air-Gapped Universal Document Ingestion Engine & Citation Spine",
    lifespan=lifespan
)


def verify_api_token(authorization: Optional[str] = Header(None)):
    """Enforce API token authentication when NEXUS_API_TOKEN is configured."""
    if not config.api_token:
        return True
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Authorization header")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or parts[1] != config.api_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API token")
    return True


# ─── Truthful Healthcheck Endpoint ───────────────────────────────────────────

@app.get("/health")
def health_check():
    """
    Diagnostic healthcheck verifying:
    - Database & pgvector extension
    - Host binaries: Poppler (pdftotext, pdftoppm) and Tesseract (tesseract)
    - Ollama host & embedding model
    - Watch queue depth & OCR backlog
    - Last recorded ingestion failure class
    """
    engine = client.engine
    db_ok = False
    vector_ok = False
    ollama_ok = False
    poppler_ok = bool(shutil.which("pdftotext") and shutil.which("pdftoppm"))
    tesseract_ok = bool(shutil.which("tesseract"))
    errors = []
    last_failure_class = None

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            db_ok = True
            if engine.dialect.name == "postgresql":
                res = conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).fetchone()
                vector_ok = bool(res)
            else:
                vector_ok = True

            # Query last failure class
            try:
                fail_row = conn.execute(
                    text("SELECT error_class FROM ingest_reports WHERE status = 'failed' ORDER BY id DESC LIMIT 1")
                ).fetchone()
                if fail_row:
                    last_failure_class = fail_row[0]
            except Exception:
                pass
    except Exception as e:
        errors.append(f"Database connection error: {e}")

    try:
        resp = httpx.get(f"{config.ollama_url}/api/version", timeout=3.0)
        ollama_ok = resp.status_code == 200
    except Exception as e:
        errors.append(f"Ollama host unreachable at {config.ollama_url}: {e}")

    if not poppler_ok:
        errors.append("Poppler utilities (pdftotext/pdftoppm) are missing from the system PATH")
    if not tesseract_ok:
        errors.append("Tesseract OCR binary is missing; scanned PDF OCR fallback is unavailable")

    # Measure queue depth
    queue_depth = 0
    watch_dir = config.watch_dir or "./ingest_watch"
    staging_dir = os.path.join(watch_dir, "staging")
    if os.path.exists(staging_dir):
        for _, _, files in os.walk(staging_dir):
            queue_depth += sum(1 for f in files if f.endswith(".part"))

    critical_healthy = db_ok and vector_ok and ollama_ok and poppler_ok
    overall_status = "healthy" if (critical_healthy and tesseract_ok) else ("degraded" if critical_healthy else "unhealthy")
    status_code = status.HTTP_200_OK if critical_healthy else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=status_code,
        content={
            "status": overall_status,
            "version": "0.2.0",
            "database": "connected" if db_ok else "disconnected",
            "pgvector_extension": "active" if vector_ok else "missing",
            "poppler": "available" if poppler_ok else "missing",
            "tesseract": "available" if tesseract_ok else "missing",
            "ollama": "connected" if ollama_ok else "unreachable",
            "embed_model": config.embed_model,
            "queue_depth": queue_depth,
            "last_failure_class": last_failure_class,
            "errors": errors
        }
    )


# ─── Ingestion Endpoints ──────────────────────────────────────────────────────

@app.post("/v1/ingest", response_model=IngestReport, dependencies=[Depends(verify_api_token)])
async def ingest_document(
    file: Optional[UploadFile] = File(None),
    filepath: Optional[str] = Form(None),
    workspace: str = Form(...),
    doc_type: str = Form("general"),
    archive: bool = Form(False)
):
    """
    Ingest a document into a workspace via multipart file upload or local filepath.
    """
    if not workspace or not workspace.strip():
        raise HTTPException(status_code=400, detail="A workspace name is required.")

    resolved_doc_type = DocType(doc_type.lower()) if doc_type.lower() in [e.value for e in DocType] else DocType.GENERAL

    if file:
        temp_dir = tempfile.mkdtemp(prefix="nexus_upload_")
        target_path = os.path.join(temp_dir, file.filename)
        try:
            with open(target_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            report = client.ingest(
                filepath=target_path,
                workspace=workspace.strip(),
                doc_type=resolved_doc_type,
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
            doc_type=resolved_doc_type,
            archive=archive
        )

    raise HTTPException(status_code=400, detail="Either a multipart 'file' or a local 'filepath' must be provided")


# ─── Search Endpoints ─────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    workspace: str = Field(..., description="Target workspace name (required)")
    doc_type: Optional[str] = None
    limit: int = Field(default=5, ge=1, le=50)
    filters: Optional[SearchFilter] = None


@app.post("/v1/search", response_model=List[SearchHit], dependencies=[Depends(verify_api_token)])
def search_corpus(req: SearchRequest):
    """
    Execute hybrid vector + full-text search across a workspace.
    Returns ranked SearchHit models with canonical citations and explainability metadata.
    """
    if not req.workspace or not req.workspace.strip():
        raise HTTPException(status_code=400, detail="Search requires a specific workspace.")

    hits = client.search(
        query=req.query,
        workspace=req.workspace.strip(),
        doc_type=req.doc_type,
        limit=req.limit,
        filters=req.filters
    )
    return hits


# ─── Document Operations ──────────────────────────────────────────────────────

@app.get("/v1/documents", response_model=List[DocumentInfo], dependencies=[Depends(verify_api_token)])
def list_documents(workspace: Optional[str] = Query(None)):
    """List all ingested documents with optional workspace filtering."""
    return client.list_documents(workspace=workspace)


@app.get("/v1/documents/{doc_id_or_hash}/report", response_model=IngestReport, dependencies=[Depends(verify_api_token)])
def get_document_ingest_report(doc_id_or_hash: str):
    """Fetch the stored IngestReport for a document."""
    report = client.get_ingest_report(doc_id_or_hash)
    if not report:
        raise HTTPException(status_code=404, detail=f"Document '{doc_id_or_hash}' not found")
    return report


@app.post("/v1/documents/{doc_id}/reparse", response_model=IngestReport, dependencies=[Depends(verify_api_token)])
def reparse_document(doc_id: int):
    """Re-parse and re-chunk an existing document in the corpus."""
    try:
        return client.reparse(doc_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Reparse failed: {e}")


@app.delete("/v1/documents/{doc_id}", dependencies=[Depends(verify_api_token)])
def delete_document(doc_id: int):
    """Delete a document and its chunks from the database."""
    success = client.delete_document(doc_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Document ID {doc_id} not found")
    return {"status": "deleted", "document_id": doc_id}


# ─── Workspace Endpoints ──────────────────────────────────────────────────────

@app.get("/v1/workspaces", response_model=List[WorkspaceInfo], dependencies=[Depends(verify_api_token)])
def list_workspaces():
    """List all workspaces and their indexed document counts."""
    return client.list_workspaces()


class CreateWorkspaceRequest(BaseModel):
    name: str
    description: Optional[str] = None


@app.post("/v1/workspaces", response_model=WorkspaceInfo, dependencies=[Depends(verify_api_token)])
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
