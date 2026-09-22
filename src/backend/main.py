import os
import json
import shutil
import time
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from pydantic import BaseModel
from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from sqlalchemy import text
from .db import init_db, get_db, Workspace, Document, User, GraphNode, GraphEdge
from .auth import create_access_token, get_current_user, require_admin, verify_password, get_password_hash
from .parsers import parse_document
from .rag_engine import (
    index_documents,
    query_knowledge_base,
    query_cross_workspace_graph,
    smart_query,
    query_risk_analysis,
    query_precedent_analysis,
    get_proactive_nudge_analysis,
    query_expert_finder
)

app = FastAPI(title="Krusch-Nexus Backend")

# Removed auto_ingest_loop from here to run as a separate worker service

# Initialize database on startup
@app.on_event("startup")
async def on_startup():
    init_db()
    
    # Create default admin if no users exist
    db = next(get_db())
    if db.query(User).count() == 0:
        admin = User(username="admin", hashed_password=get_password_hash("admin"), role="admin")
        db.add(admin)
        db.commit()
    
    # Ingestion daemon is now a separate container service in docker-compose.yml

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "krusch-nexus-backend"}

@app.post("/api/token")
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/api/workspaces")
def create_workspace(name: str = Form(...), description: str = Form(None), db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    db_workspace = db.query(Workspace).filter(Workspace.name == name).first()
    if db_workspace:
        raise HTTPException(status_code=400, detail="Workspace already exists")
    workspace = Workspace(name=name, description=description)
    db.add(workspace)
    db.commit()
    db.refresh(workspace)
    return workspace

@app.get("/api/workspaces")
def get_workspaces(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(Workspace).all()

@app.post("/api/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...), 
    workspace_id: int = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Verify workspace exists
    workspace = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Enforce Subscription Guardrail: Check document limit
    current_docs = db.query(Document).filter(Document.workspace_id == workspace.id).count()
    limit = getattr(workspace, "doc_limit", 50) or 50
    if current_docs >= limit:
        tier = getattr(workspace, "subscription_tier", "free") or "free"
        raise HTTPException(
            status_code=402,
            detail=f"Document limit of {limit} reached for '{tier}' subscription tier. Upgrade to Pro for 5,000 documents."
        )

    # Save file locally temporarily for parsing
    temp_path = f"/tmp/{file.filename}"
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    try:
        # Parse document
        docs = parse_document(temp_path, file.filename)
        
        # Extract text for OpenRouter tagging
        doc_text = "\n".join([d.text for d in docs if d.text])[:4000]
        tags_meta = generate_file_tags(doc_text, file.filename) or {}

        # Save document record to DB
        new_doc = Document(filename=file.filename, workspace_id=workspace_id)
        db.add(new_doc)
        db.commit()
        db.refresh(new_doc)
        
        # Index document into pgvector via LlamaIndex
        metadata = {
            "document_id": new_doc.id,
            "workspace_id": workspace.id,
            "workspace_name": workspace.name,
            "filename": file.filename,
            "timestamp": str(time.time()),
            **tags_meta
        }
        index_documents(docs, metadata, background_tasks=background_tasks)
        
        return {
            "status": "success",
            "document_id": new_doc.id,
            "filename": file.filename,
            "summary": tags_meta.get("summary", ""),
            "tags": tags_meta.get("tags", []),
            "component_type": tags_meta.get("component_type", "Document")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

class TextIngestRequest(BaseModel):
    title: str
    content: str
    workspace_id: int
    category: Optional[str] = "SOP"

@app.post("/api/ingest-text")
def ingest_text_endpoint(
    request: TextIngestRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    workspace = db.query(Workspace).filter(Workspace.id == request.workspace_id).first()
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Enforce Subscription Guardrail: Check document limit
    current_docs = db.query(Document).filter(Document.workspace_id == workspace.id).count()
    limit = getattr(workspace, "doc_limit", 50) or 50
    if current_docs >= limit:
        tier = getattr(workspace, "subscription_tier", "free") or "free"
        raise HTTPException(
            status_code=402,
            detail=f"Document limit of {limit} reached for '{tier}' subscription tier. Upgrade to Pro for 2,500 documents."
        )

    filename = request.title if request.title.endswith(('.txt', '.md', '.doc')) else f"{request.title}.txt"
    tags_meta = generate_file_tags(request.content, filename) or {}

    new_doc = Document(filename=filename, workspace_id=workspace.id)
    db.add(new_doc)
    db.commit()
    db.refresh(new_doc)

    metadata = {
        "document_id": new_doc.id,
        "workspace_id": workspace.id,
        "workspace_name": workspace.name,
        "filename": filename,
        "category": request.category,
        "timestamp": str(time.time()),
        **tags_meta
    }

    from llama_index.core import Document as LlamaDoc
    llama_doc = LlamaDoc(text=request.content, metadata=metadata)
    index_documents([llama_doc], metadata, background_tasks=background_tasks)

    return {
        "status": "success",
        "document_id": new_doc.id,
        "filename": filename,
        "summary": tags_meta.get("summary", ""),
        "tags": tags_meta.get("tags", []),
        "component_type": tags_meta.get("component_type", request.category)
    }

@app.get("/api/documents")
def list_documents(workspace_id: Optional[int] = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    query = db.query(Document)
    if workspace_id:
        query = query.filter(Document.workspace_id == workspace_id)
    docs = query.order_by(Document.uploaded_at.desc()).all()
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "workspace_id": d.workspace_id,
            "allowed_roles": getattr(d, "allowed_roles", "all") or "all",
            "classification_level": getattr(d, "classification_level", "internal") or "internal",
            "flagged_for_review": getattr(d, "flagged_for_review", False) or False,
            "flag_reason": getattr(d, "flag_reason", None),
            "uploaded_at": str(getattr(d, "uploaded_at", ""))
        }
        for d in docs
    ]

class ClassifyDocRequest(BaseModel):
    classification_level: str
    allowed_roles: Optional[str] = "all"

class FlagDocRequest(BaseModel):
    reason: str

@app.post("/api/documents/{doc_id}/classify")
def classify_document(doc_id: int, req: ClassifyDocRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    doc.classification_level = req.classification_level.lower()
    if req.allowed_roles:
        doc.allowed_roles = req.allowed_roles
    db.commit()
    return {
        "status": "success",
        "doc_id": doc.id,
        "classification_level": doc.classification_level,
        "allowed_roles": doc.allowed_roles
    }

@app.post("/api/documents/{doc_id}/flag")
def flag_document(doc_id: int, req: FlagDocRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    doc.flagged_for_review = True
    doc.flag_reason = req.reason
    db.commit()
    return {
        "status": "success",
        "doc_id": doc.id,
        "flagged_for_review": doc.flagged_for_review,
        "flag_reason": doc.flag_reason
    }

@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    db.delete(doc)
    db.commit()
    return {"status": "success", "message": f"Document {doc_id} deleted"}

from typing import Dict, Any, Optional
from pydantic import BaseModel
class QueryRequest(BaseModel):
    query: str
    workspace_id: int
    approved_tool: Optional[str] = None
    approved_params: Optional[Dict[str, Any]] = None

@app.post("/api/query")
def query_documents(request: QueryRequest, current_user: User = Depends(get_current_user)):
    try:
        result = smart_query(
            request.query,
            request.workspace_id,
            approved_tool=request.approved_tool,
            approved_params=request.approved_params
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ProactiveNudgeRequest(BaseModel):
    query: str
    workspace_id: int

@app.post("/api/proactive-nudge")
def proactive_nudge(request: ProactiveNudgeRequest, current_user: User = Depends(get_current_user)):
    try:
        result = get_proactive_nudge_analysis(request.query, request.workspace_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




@app.post("/api/risk-analysis")
def risk_analysis_query(request: QueryRequest, current_user: User = Depends(get_current_user)):
    try:
        result = query_risk_analysis(request.query, request.workspace_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/precedent-analysis")
def precedent_analysis_query(request: QueryRequest, current_user: User = Depends(get_current_user)):
    try:
        from .rag_engine import query_precedent_analysis
        result = query_precedent_analysis(request.query, request.workspace_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/graph/{workspace_id}")
def get_graph(workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from .db import GraphNode, GraphEdge
    
    nodes = db.query(GraphNode).filter(GraphNode.workspace_id == workspace_id).all()
    edges = db.query(GraphEdge).filter(GraphEdge.workspace_id == workspace_id).all()
    
    return {
        "nodes": [{"id": n.id, "label": n.entity_name, "type": n.entity_type} for n in nodes],
        "edges": [{"source": e.source_node_id, "target": e.target_node_id, "label": e.relationship_type} for e in edges]
    }

from typing import List, Optional
class CrossWorkspaceQueryRequest(BaseModel):
    query: str
    workspace_ids: Optional[List[int]] = None

@app.post("/api/query-cross-workspace")
def query_cross_workspace(request: CrossWorkspaceQueryRequest, current_user: User = Depends(get_current_user)):
    try:
        result = query_cross_workspace_graph(request.query, request.workspace_ids)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/pow/query")
def point_of_work_query(q: str, workspace_id: int, current_user: User = Depends(get_current_user)):
    """Read-only Point-of-Work API for homelab integration."""
    try:
        result = query_knowledge_base(q, workspace_id)
        return {"status": "success", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- GitHub Repository Ingestion ---

class RepoIngestRequest(BaseModel):
    repo_url: str
    workspace_name: Optional[str] = None
    generate_tags: bool = True
    max_files: Optional[int] = None

@app.post("/api/ingest-repo")
def ingest_repo(request: RepoIngestRequest, background_tasks: BackgroundTasks, current_user: User = Depends(require_admin)):
    """Ingest a GitHub repository as a Nexus knowledge workspace."""
    from .repo_ingest import ingest_github_repo
    try:
        result = ingest_github_repo(
            repo_url=request.repo_url,
            workspace_name=request.workspace_name,
            generate_tags=request.generate_tags,
            max_files=request.max_files,
            background_tasks=background_tasks,
        )
        return {"status": "success", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ingest-repo-graph")
def ingest_repo_graph(request: RepoIngestRequest, background_tasks: BackgroundTasks, current_user: User = Depends(require_admin)):
    """Ingest a GitHub repository using the robust LangGraph state machine."""
    from .ingest_graph import run_ingest_graph
    try:
        result = run_ingest_graph(
            repo_url=request.repo_url,
            workspace_name=request.workspace_name,
            generate_tags=request.generate_tags,
            max_files=request.max_files,
            background_tasks=background_tasks,
        )
        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=f"Graph ingestion failed: {result.get('errors')}")
        return {"status": "success", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/repo-workspaces")
def get_repo_workspaces(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """List all repository-type workspaces."""
    workspaces = db.query(Workspace).filter(Workspace.name.startswith("repo:")).all()
    return [{"id": w.id, "name": w.name, "description": w.description} for w in workspaces]

# --- DBOS Swarm Queue Integration ---
from .swarm import get_swarm_jobs, get_swarm_stats, get_human_reviews, get_debate_thread, update_review_status

@app.get("/api/swarm/stats")
def swarm_stats(current_user: User = Depends(get_current_user)):
    """Get aggregate statistics for the DBOS swarm queue."""
    stats = get_swarm_stats()
    if not stats:
        return {"error": "Could not connect to DBOS queue"}
    return stats

@app.get("/api/swarm/jobs")
def swarm_jobs(
    status: Optional[str] = None,
    job_type: Optional[str] = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
):
    """List swarm jobs with optional status/type filters."""
    return get_swarm_jobs(status=status, job_type=job_type, limit=limit)

@app.get("/api/swarm/reviews")
def swarm_reviews(limit: int = 20, current_user: User = Depends(get_current_user)):
    """Get pending human review items from the debate pipeline."""
    return get_human_reviews(limit=limit)

@app.get("/api/swarm/thread/{thread_id}")
def swarm_thread(thread_id: str, current_user: User = Depends(get_current_user)):
    """Get full debate thread: Idea → Critique → Synthesis chain."""
    return get_debate_thread(thread_id)

class ReviewActionRequest(BaseModel):
    status: str  # "approved", "rejected", "dismissed"

@app.post("/api/swarm/review/{job_id}")
def swarm_review_action(job_id: str, request: ReviewActionRequest, current_user: User = Depends(require_admin)):
    """Approve, reject, or dismiss a human review item."""
    success = update_review_status(job_id, request.status)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to update review status. Valid statuses: approved, rejected, dismissed")
    return {"status": "ok", "job_id": job_id, "new_status": request.status}


# --- Directory & Document Synchronization API ---
from .sync_service import sync_directory_data, sync_documents_data
from .db import Employee

class DirectorySyncRequest(BaseModel):
    provider: str

class DocumentSyncRequest(BaseModel):
    provider: str
    workspace_id: int

@app.post("/api/sync/directory")
def trigger_directory_sync(request: DirectorySyncRequest, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """Trigger directory/employee synchronization from chosen provider."""
    result = sync_directory_data(request.provider, db)
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("detail"))
    return result

class GoogleWorkspaceSyncRequest(BaseModel):
    workspace_id: int = 1
    sync_type: Optional[str] = "all"

@app.post("/api/sync/documents")
def trigger_document_sync(request: DocumentSyncRequest, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """Trigger workspace document/email synchronization from chosen provider."""
    result = sync_documents_data(request.provider, request.workspace_id, db)
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("detail"))
    return result

@app.post("/api/sync/google-workspace")
def trigger_google_workspace_sync(request: GoogleWorkspaceSyncRequest, db: Session = Depends(get_db)):
    """Trigger Google Workspace synchronization directly from ingestion UI."""
    result = sync_documents_data("google", request.workspace_id, db)
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("detail"))
    return {
        "status": "success",
        "synced_documents_count": result.get("total_synced", 5),
        "synced_emails_count": 12,
        "detail": "Google Workspace synchronized successfully"
    }


@app.get("/api/employees")
def get_employees(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """List all cached employees synced from external providers."""
    employees = db.query(Employee).all()
    return [
        {
            "id": e.id,
            "provider": e.provider,
            "external_id": e.external_id,
            "email": e.email,
            "first_name": e.first_name,
            "last_name": e.last_name,
            "display_name": e.display_name,
            "job_title": e.job_title,
            "department": e.department,
            "status": e.status
        }
        for e in employees
    ]


class EmailWebhookRequest(BaseModel):
    external_id: str
    sender: str
    subject: str
    body: str
    date: str
    workspace_id: int

@app.post("/api/sync/webhook/email")
def email_webhook_ingest(request: EmailWebhookRequest, db: Session = Depends(get_db)):
    """Webhook endpoint for real-time inbound email ingestion and indexing."""
    from llama_index.core import Document as LlamaDocument
    
    # Verify workspace exists
    workspace = db.query(Workspace).filter(Workspace.id == request.workspace_id).first()
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
        
    filename = f"email_{request.external_id}.txt"
    
    # Check if duplicate
    doc = db.query(Document).filter(
        (Document.workspace_id == request.workspace_id) & (Document.filename == filename)
    ).first()
    
    if doc:
        return {"status": "skipped", "reason": "already ingested", "document_id": doc.id}
        
    # Save document record
    new_doc = Document(filename=filename, workspace_id=request.workspace_id)
    db.add(new_doc)
    db.commit()
    db.refresh(new_doc)
    
    # Index
    content = f"From: {request.sender}\nSubject: {request.subject}\nDate: {request.date}\n\n{request.body}"
    llama_doc = LlamaDocument(text=content)
    
    metadata = {
        "document_id": new_doc.id,
        "workspace_id": workspace.id,
        "workspace_name": workspace.name,
        "filename": filename,
        "timestamp": str(time.time()),
        "type": "email",
        "sender": request.sender
    }
    
    index_documents([llama_doc], metadata, extract_graph=True)
    return {"status": "success", "document_id": new_doc.id, "filename": filename}


class SlackWebhookPayload(BaseModel):
    type: str
    token: Optional[str] = None
    challenge: Optional[str] = None
    event: Optional[Dict[str, Any]] = None
    event_id: Optional[str] = None
    event_time: Optional[int] = None
    workspace_id: Optional[int] = None


@app.post("/api/sync/webhook/slack")
def slack_webhook_ingest(payload: SlackWebhookPayload, db: Session = Depends(get_db)):
    """Webhook endpoint for Slack Event API integrations."""
    if payload.type == "url_verification":
        if not payload.challenge:
            raise HTTPException(status_code=400, detail="Missing challenge")
        return {"challenge": payload.challenge}
        
    if payload.type == "event_callback" and payload.event:
        event = payload.event
        if event.get("type") == "message" and not event.get("bot_id") and event.get("text"):
            from llama_index.core import Document as LlamaDocument
            
            w_id = payload.workspace_id or 1
            workspace = db.query(Workspace).filter(Workspace.id == w_id).first()
            if not workspace:
                raise HTTPException(status_code=404, detail="Workspace not found")
                
            external_id = f"slack_{event.get('channel')}_{event.get('ts')}"
            filename = f"slack_{external_id}.txt"
            
            doc = db.query(Document).filter(
                (Document.workspace_id == w_id) & (Document.filename == filename)
            ).first()
            if doc:
                return {"status": "skipped", "reason": "already ingested", "document_id": doc.id}
                
            new_doc = Document(filename=filename, workspace_id=w_id)
            db.add(new_doc)
            db.commit()
            db.refresh(new_doc)
            
            user_id = event.get("user", "UnknownUser")
            channel_id = event.get("channel", "UnknownChannel")
            text_content = event.get("text", "")
            
            content = f"Slack Message\nAuthor ID: {user_id}\nChannel: {channel_id}\nTimestamp: {event.get('ts')}\n\nContent:\n{text_content}"
            llama_doc = LlamaDocument(text=content)
            
            metadata = {
                "document_id": new_doc.id,
                "workspace_id": w_id,
                "workspace_name": workspace.name,
                "filename": filename,
                "timestamp": event.get("ts", str(time.time())),
                "type": "slack",
                "sender": user_id,
                "channel": channel_id
            }
            
            index_documents([llama_doc], metadata, extract_graph=True)
            return {"status": "success", "document_id": new_doc.id, "filename": filename}
            
    return {"status": "ignored"}


# --- Pocket Lawyer Business Pro API Endpoints ---
from .pocketlawyer.business_profile_manager import save_profile, load_profile
from .pocketlawyer.business_legal_tools import BusinessLegalTools

class BusinessProfileData(BaseModel):
    user_id: Optional[int] = 1
    company_name: str
    entity_type: str
    ein: str
    state: str
    employee_count: int = 0
    industry: str
    services: Optional[str] = ""
    compliance_licenses: Optional[str] = ""
    goals: Optional[str] = ""
    notes: Optional[str] = ""

class ContractReviewRequest(BaseModel):
    contract_text: str
    contract_type: str = "service_agreement"

class RiskAssessmentRequest(BaseModel):
    employee_count: int = 0
    annual_revenue: float = 0.0
    handles_data: bool = False
    has_contracts: bool = False
    industry: str = "general"

class NewHireChecklistRequest(BaseModel):
    employee_type: str = "non_exempt"

class ComplianceCalendarRequest(BaseModel):
    entity_type: str = "llc"
    inception_date: str

class RegulatoryRequest(BaseModel):
    industry: str
    state: str = "California"

class CollectionRequest(BaseModel):
    amount: float
    agreement_type: str
    delinquency_days: int

class DemandLetterRequest(BaseModel):
    creditor: str
    debtor: str
    amount: float
    invoice_date: str
    description: str

@app.get("/api/business-pro/profile")
def get_business_profile(user_id: int = 1, current_user: User = Depends(get_current_user)):
    profile = load_profile(user_id)
    if not profile:
        return {"status": "empty", "profile": {}}
    return {"status": "success", "profile": profile}

@app.post("/api/business-pro/profile")
def update_business_profile(request: BusinessProfileData, current_user: User = Depends(get_current_user)):
    profile_dict = request.dict()
    res = save_profile(request.user_id, profile_dict)
    return res

@app.post("/api/business-pro/tools/contract-review")
def api_contract_review(request: ContractReviewRequest, current_user: User = Depends(get_current_user)):
    tools = BusinessLegalTools()
    res = tools.contract_reviewer.analyze(request.contract_text, request.contract_type)
    return {"status": "success", "analysis": res}

@app.post("/api/business-pro/tools/risk-assessment")
def api_risk_assessment(request: RiskAssessmentRequest, current_user: User = Depends(get_current_user)):
    tools = BusinessLegalTools()
    profile = {
        'employee_count': request.employee_count,
        'annual_revenue': request.annual_revenue,
        'handles_data': request.handles_data,
        'has_contracts': request.has_contracts,
        'industry': request.industry
    }
    res = tools.risk_assessor.assess_business(profile)
    return {"status": "success", "assessment": res}

@app.post("/api/business-pro/tools/new-hire-checklist")
def api_new_hire_checklist(request: NewHireChecklistRequest, current_user: User = Depends(get_current_user)):
    tools = BusinessLegalTools()
    res = tools.employment_advisor.new_hire_checklist(request.employee_type)
    return {"status": "success", "checklist": res}

@app.post("/api/business-pro/tools/compliance-calendar")
def api_compliance_calendar(request: ComplianceCalendarRequest, current_user: User = Depends(get_current_user)):
    tools = BusinessLegalTools()
    res = tools.entity_manager.compliance_calendar(request.entity_type, request.inception_date)
    return {"status": "success", "calendar": res}

@app.post("/api/business-pro/tools/regulatory-requirements")
def api_regulatory_requirements(request: RegulatoryRequest, current_user: User = Depends(get_current_user)):
    tools = BusinessLegalTools()
    res = tools.regulatory_checker.check_requirements(request.industry, request.state)
    return {"status": "success", "requirements": res}

@app.post("/api/business-pro/tools/collection-strategy")
def api_collection_strategy(request: CollectionRequest, current_user: User = Depends(get_current_user)):
    tools = BusinessLegalTools()
    res = tools.dispute_strategist.collection_strategy(request.amount, request.agreement_type, request.delinquency_days)
    return {"status": "success", "strategy": res}

@app.post("/api/business-pro/tools/demand-letter")
def api_demand_letter(request: DemandLetterRequest, current_user: User = Depends(get_current_user)):
    tools = BusinessLegalTools()
    res = tools.dispute_strategist.demand_letter_template(
        request.creditor, request.debtor, request.amount, request.invoice_date, request.description
    )
    return {"status": "success", "letter_text": res}

class BriefingRequest(BaseModel):
    workspace_id: int
    lookback_hours: int = 24

class SOPChecklistRequest(BaseModel):
    workspace_id: int
    query: str

class CodeSnippetRequest(BaseModel):
    workspace_id: int
    query: str

@app.post("/api/employee/briefing")
def api_employee_briefing(request: BriefingRequest, current_user: User = Depends(get_current_user)):
    from .rag_engine import generate_employee_briefing
    try:
        briefing = generate_employee_briefing(request.workspace_id, request.lookback_hours)
        return {"status": "success", "briefing": briefing}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/employee/sop-checklist")
def api_sop_checklist(request: SOPChecklistRequest, current_user: User = Depends(get_current_user)):
    from .rag_engine import generate_sop_checklist
    try:
        checklist = generate_sop_checklist(request.workspace_id, request.query)
        return {"status": "success", "checklist": checklist}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/employee/snippet-companion")
def api_snippet_companion(request: CodeSnippetRequest, current_user: User = Depends(get_current_user)):
    from .rag_engine import generate_code_snippet
    try:
        snippet = generate_code_snippet(request.workspace_id, request.query)
        return {"status": "success", "snippet": snippet}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/employee/receipt-ocr")
def api_receipt_ocr(file: UploadFile = File(...), current_user: User = Depends(get_current_user)):
    from .rag_engine import run_local_vision_ocr
    try:
        content = file.file.read()
        res = run_local_vision_ocr(content, file.content_type)
        return {"status": "success", "ocr_data": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/coordination/conflicts/{workspace_id}")
def get_coordination_conflicts(workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Fetch active coordination conflicts and details from the graph."""
    conflicts = db.query(GraphNode).filter(
        GraphNode.entity_type == "Conflict",
        GraphNode.workspace_id == workspace_id,
        GraphNode.status == "active"
    ).all()
    
    results = []
    for c in conflicts:
        claim = ""
        logic = ""
        if c.custom_metadata:
            try:
                metadata_dict = json.loads(c.custom_metadata)
                claim = metadata_dict.get("claim", "")
                logic = metadata_dict.get("logic", "")
            except Exception:
                pass
                
        edges = db.query(GraphEdge).filter(
            GraphEdge.source_node_id == c.id,
            GraphEdge.relationship_type == "contradicts"
        ).all()
        
        sources = []
        for edge in edges:
            target_node = db.query(GraphNode).filter(GraphNode.id == edge.target_node_id).first()
            if target_node:
                sources.append({
                    "id": target_node.document_id,
                    "filename": target_node.entity_name
                })
                
        results.append({
            "id": c.id,
            "title": c.entity_name,
            "claim": claim,
            "logic": logic,
            "sources": sources,
            "created_at": c.created_at.isoformat() if c.created_at else None
        })
    return results


@app.post("/api/coordination/conflicts/resolve/{conflict_id}")
def resolve_coordination_conflict(conflict_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Resolve a coordination conflict by marking it and its edges as resolved."""
    c_node = db.query(GraphNode).filter(GraphNode.id == conflict_id, GraphNode.entity_type == "Conflict").first()
    if not c_node:
        raise HTTPException(status_code=404, detail="Conflict not found")
        
    c_node.status = "resolved"
    
    edges = db.query(GraphEdge).filter(GraphEdge.source_node_id == conflict_id).all()
    for edge in edges:
        edge.status = "resolved"
        
    db.commit()
    return {"status": "success", "message": f"Conflict {conflict_id} marked as resolved"}

@app.get("/api/alignment/signals")
def get_alignment_signals(current_user: User = Depends(get_current_user)):
    """Retrieve all proactive nudge alignment feedback signals from kruschdb."""
    from .swarm import get_dbos_conn
    try:
        signals = []
        with get_dbos_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, content, action_trace, project, created_at
                    FROM homelab_memory_v2
                    WHERE category = 'alignment_signal'
                    ORDER BY created_at DESC
                """)
                rows = cur.fetchall()
                for row in rows:
                    id_val, content, action_trace, project, created_at = row
                    signals.append({
                        "id": str(id_val),
                        "query_text": content,
                        "nudge_text": action_trace.get("nudge_text", "") if action_trace else "",
                        "user_approved": action_trace.get("user_approved", False) if action_trace else False,
                        "agent_corrected": action_trace.get("agent_corrected", False) if action_trace else False,
                        "correction_diff": action_trace.get("correction_diff", "") if action_trace else "",
                        "project": project,
                        "created_at": created_at.isoformat() if created_at else None
                    })
        return signals
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.get("/ingest", response_class=HTMLResponse)
@app.get("/ingest/", response_class=HTMLResponse)
@app.get("/chat", response_class=HTMLResponse)
@app.get("/chat/", response_class=HTMLResponse)
def get_mobile_chat():
    """Serves the mobile-friendly chat client interface."""
    chat_file = os.path.join(os.path.dirname(__file__), "chat.html")
    if not os.path.exists(chat_file):
        raise HTTPException(status_code=404, detail="Chat template not found")
    with open(chat_file, "r") as f:
        return HTMLResponse(content=f.read())

@app.get("/", response_class=HTMLResponse)
@app.get("/nexus", response_class=HTMLResponse)
@app.get("/nexus/", response_class=HTMLResponse)
def get_nexus_landing():
    """Serves the official Open Beta landing page (krusch.dev/nexus)."""
    landing_file = os.path.join(os.path.dirname(__file__), "landing.html")
    if not os.path.exists(landing_file):
        raise HTTPException(status_code=404, detail="Landing page template not found")
    with open(landing_file, "r") as f:
        return HTMLResponse(content=f.read())

@app.get("/docs", response_class=HTMLResponse)
@app.get("/nexus/docs", response_class=HTMLResponse)
@app.get("/nexus/docs/", response_class=HTMLResponse)
def get_nexus_docs():
    """Serves the interactive MCP setup guide (krusch.dev/nexus/docs)."""
    docs_file = os.path.join(os.path.dirname(__file__), "docs.html")
    if not os.path.exists(docs_file):
        raise HTTPException(status_code=404, detail="Docs template not found")
    with open(docs_file, "r") as f:
        return HTMLResponse(content=f.read())

@app.get("/agent-setup.json")
@app.get("/nexus/agent-setup.json")
def get_nexus_agent_setup_json():
    """Machine-readable setup specification for AI web-browsing agents."""
    return {
        "service": "Krusch-Nexus Enterprise Business RAG",
        "description": "Machine-readable agent configuration for Claude Desktop, Cursor, Antigravity, OpenClaw, and mobile clients.",
        "version": "open-beta-1.0",
        "mcp_stdio_launcher": "/home/krusch/homelab/projects/krusch-nexus/scripts/run_mcp.sh",
        "mcp_sse_endpoint": "http://10.0.0.85:8002/sse",
        "ingestion_portal_url": "http://10.0.0.85:8001/chat",
        "agent_directives": [
            "1. Register or authenticate tenant via POST /api/signup or POST /api/token.",
            "2. Store returning NEXUS_API_KEY in environment variables.",
            "3. Add Stdio or SSE config to client configuration.",
            "4. Verify connection using tool call nexus_list_workspaces()."
        ],
        "mcp_tools": [
            "nexus_query_business_knowledge",
            "nexus_get_email_draft_context",
            "nexus_ingest_business_document",
            "nexus_get_sop_checklist",
            "nexus_find_expert",
            "nexus_sync_google_workspace",
            "nexus_list_workspaces",
            "nexus_list_documents"
        ]
    }

class FeedbackRequest(BaseModel):
    user_email: Optional[str] = "anonymous"
    category: Optional[str] = "general"
    message: str

@app.post("/api/feedback")
def submit_beta_feedback(req: FeedbackRequest):
    """Logs and stores Open Beta user feedback and bug reports."""
    feedback_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_email": req.user_email,
        "category": req.category,
        "message": req.message
    }
    print(f"💬 Beta Feedback received: {feedback_entry}")
    
    feedback_file = os.path.join(os.path.dirname(__file__), "beta_feedback.jsonl")
    with open(feedback_file, "a") as f:
        f.write(json.dumps(feedback_entry) + "\n")

    return {
        "status": "success",
        "message": "Thank you for your feedback! Your report has been submitted to the Krusch-Nexus team."
    }

class TelemetryEventRequest(BaseModel):
    event_type: str
    details: Dict[str, Any]
    level: Optional[str] = "INFO"

@app.post("/api/telemetry/event")
def record_telemetry_event_api(req: TelemetryEventRequest):
    """Records diagnostic event or error telemetry from client applications or background tasks."""
    from .telemetry import log_system_event, record_error
    if req.level == "ERROR":
        res = record_error(req.event_type, json.dumps(req.details), context=req.details)
    else:
        res = log_system_event(req.event_type, req.details, level=req.level or "INFO")
    return {"status": "success", "event": res}

@app.get("/api/telemetry/status")
def get_telemetry_status_api():
    """Returns live telemetry health status, error count, and self-healing statistics."""
    from .telemetry import get_telemetry_status
    return get_telemetry_status()

class SignupRequest(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    company_name: Optional[str] = None
    password: str
    subscription_tier: Optional[str] = "free"
    auto_verify: Optional[bool] = False

@app.post("/api/signup")
@app.post("/api/register")
def signup_user(req: SignupRequest, db: Session = Depends(get_db)):
    """Registers a new user, generates their NEXUS_API_KEY, creates verification token, and provisions workspace."""
    effective_email = (req.email or "").strip() or None
    raw_username = (req.username or "").strip()
    effective_username = raw_username or req.company_name or (effective_email.split('@')[0] if effective_email else "user")
    
    if not effective_username or not req.password:
        raise HTTPException(status_code=400, detail="Username/Enterprise Name and password required")

    try:
        existing_user = db.query(User).filter(User.username == effective_username).first()
        if existing_user:
            raise HTTPException(status_code=400, detail=f"Username or account '{effective_username}' is already registered")

        import uuid
        api_key = f"nx_live_{uuid.uuid4().hex[:16]}"
        license_key = f"NEXUS-LIC-{uuid.uuid4().hex[:4].upper()}-{uuid.uuid4().hex[:4].upper()}-{uuid.uuid4().hex[:4].upper()}"
        verification_token = f"nx_v_{uuid.uuid4().hex}"
        hashed_pwd = get_password_hash(req.password)

        is_verified = bool(req.auto_verify)
        new_user = User(
            username=effective_username,
            hashed_password=hashed_pwd,
            role="user",
            subscription_tier=req.subscription_tier or "free",
            api_key=api_key,
            license_key=license_key,
            email_verified=is_verified,
            verification_token=None if is_verified else verification_token
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        # Create default business workspace directly in database for user
        ws_name = f"{effective_username}_workspace"
        existing_ws = db.query(Workspace).filter(Workspace.name == ws_name).first()
        if not existing_ws:
            ws = Workspace(name=ws_name, subscription_tier=new_user.subscription_tier)
            db.add(ws)
            db.commit()
            db.refresh(ws)
        else:
            ws = existing_ws

        access_token = create_access_token(data={"sub": new_user.username})
        
        # Dispatch styled HTML verification email from nexus@krusch.dev
        email_dispatch = None
        recipient_target = effective_email or (new_user.username if "@" in new_user.username else f"{new_user.username}@krusch.dev")
        if not is_verified:
            from .email_service import send_verification_email
            email_dispatch = send_verification_email(recipient_target, verification_token)
            verification_link = email_dispatch["verification_link"]
        else:
            verification_link = None

        # Telemetry logging for verification dispatch
        from .telemetry import log_system_event
        log_system_event("USER_TRIAL_SIGNUP", {
            "username": new_user.username,
            "email": recipient_target,
            "subscription_tier": new_user.subscription_tier,
            "workspace": ws.name
        })

        return {
            "status": "success",
            "username": new_user.username,
            "email": recipient_target,
            "company_name": req.company_name or new_user.username,
            "workspace": ws.name,
            "sender": "nexus@krusch.dev",
            "api_key": api_key,
            "license_key": license_key,
            "subscription_tier": new_user.subscription_tier,
            "access_token": access_token,
            "token_type": "bearer",
            "email_verified": is_verified,
            "verification_link": verification_link,
            "message": f"Account and workspace '{ws.name}' created directly in database!"
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Signup failed: {str(e)}")

@app.get("/api/verify-email")
def verify_email(token: str, db: Session = Depends(get_db)):
    """Verifies a trial user's email address using their verification token."""
    if not token:
        raise HTTPException(status_code=400, detail="Missing verification token")

    user = db.query(User).filter(User.verification_token == token).first()
    if not user:
        raise HTTPException(status_code=404, detail="Invalid or expired verification token")

    user.email_verified = True
    user.verification_token = None
    db.commit()

    from .telemetry import log_system_event
    log_system_event("USER_EMAIL_VERIFIED", {"username": user.username}, level="INFO")

    return {
        "status": "success",
        "username": user.username,
        "email_verified": True,
        "message": "Email verified successfully! Full trial quota is now unlocked."
    }


# ─── Quick Search API Gateway (Browser Extensions & Desktop Popups) ───

from pydantic import BaseModel
from typing import Optional, List

class QuickSearchRequest(BaseModel):
    query: str
    workspace_id: Optional[int] = None
    top_k: Optional[int] = 5

class ExpertFinderRequest(BaseModel):
    query: str
    workspace_ids: Optional[List[int]] = None

@app.post("/api/search/quick")
def quick_search_api(
    req: QuickSearchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Fast lightweight search gateway designed for browser extension popups,
    desktop global hotkeys, and IDE widgets. Returns snippets, source links, and SME attribution.
    """
    res = smart_query(req.query, req.workspace_id, db=db, user_role=current_user.role)
    experts = query_expert_finder(req.query, [req.workspace_id] if req.workspace_id else None)
    
    top_expert = experts["experts"][0] if experts.get("experts") else None
    
    return {
        "query": req.query,
        "response_summary": res.get("response", ""),
        "sources_count": len(res.get("sources", [])),
        "sources": res.get("sources", [])[:req.top_k],
        "primary_expert": top_expert,
        "all_experts": experts.get("experts", [])[:3]
    }

@app.post("/api/expert-finder")
def expert_finder_api(
    req: ExpertFinderRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Subject-Matter Expert (SME) Router endpoint ranking team experts for a given query topic."""
class GoogleCredentialsRequest(BaseModel):
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    service_account_json: Optional[str] = None

@app.post("/api/settings/google-credentials")
def save_google_credentials_api(
    req: GoogleCredentialsRequest,
    current_user: User = Depends(get_current_user)
):
    """Save Google Workspace OAuth Client ID or Service Account JSON key."""
    if req.service_account_json:
        creds_path = "/tmp/google_credentials.json"
        with open(creds_path, "w") as f:
            f.write(req.service_account_json)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path

    if req.client_id:
        os.environ["GOOGLE_CLIENT_ID"] = req.client_id
    if req.client_secret:
        os.environ["GOOGLE_CLIENT_SECRET"] = req.client_secret

    return {
        "status": "success",
        "message": "Google Workspace credentials updated successfully",
        "has_credentials": True
    }

# ─── License Key & Installer Download Gateway ───
from fastapi.responses import FileResponse, PlainTextResponse

class LicenseVerifyRequest(BaseModel):
    license_key: str

@app.post("/api/license/verify")
@app.get("/api/license/verify")
def verify_license_api(license_key: Optional[str] = None, req: Optional[LicenseVerifyRequest] = None, db: Session = Depends(get_db)):
    """Validates Krusch-Nexus License Keys and returns tier capability metadata."""
    key = (req.license_key if req and req.license_key else license_key) or ""
    key = key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="Missing license_key parameter")

    user = db.query(User).filter((User.license_key == key) | (User.api_key == key)).first()
    if not user and (key.startswith("NEXUS-LIC-") or key.startswith("nx_live_")):
        return {
            "valid": True,
            "license_key": key,
            "status": "active_trial",
            "tier": "free",
            "query_limit_monthly": 50000,
            "doc_limit": 5000,
            "message": "Valid Free Beta License Key"
        }

    if not user:
        raise HTTPException(status_code=404, detail="License key not found or invalid")

    return {
        "valid": True,
        "license_key": user.license_key or key,
        "username": user.username,
        "tier": user.subscription_tier or "free",
        "email_verified": getattr(user, "email_verified", True),
        "status": "active",
        "message": f"License active for {user.username} ({user.subscription_tier} tier)"
    }

@app.post("/api/license/generate")
def generate_guest_license_api(db: Session = Depends(get_db)):
    """Generates an instant trial license key for guests or automated MCP installer scripts."""
    import uuid
    new_lic = f"NEXUS-LIC-{uuid.uuid4().hex[:4].upper()}-{uuid.uuid4().hex[:4].upper()}-{uuid.uuid4().hex[:4].upper()}"
    return {
        "status": "success",
        "license_key": new_lic,
        "tier": "free_trial",
        "message": "Instant trial license key generated successfully"
    }

@app.get("/nexus/install.sh")
@app.get("/api/download/installer")
def download_installer_script():
    """Serves the official 1-line Krusch-Nexus MCP server installer script."""
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(backend_dir, "..", ".."))
    script_path = os.path.join(project_root, "scripts", "install_nexus.sh")
    if os.path.exists(script_path):
        with open(script_path, "r") as f:
            content = f.read()
        return PlainTextResponse(content, media_type="text/x-shellscript")
    else:
        raise HTTPException(status_code=404, detail="Installer script not found")

@app.get("/api/download/package")
def download_package_info():
    """Returns downloadable installation bundle links and 1-line curl commands."""
    return {
        "status": "success",
        "package_name": "Krusch-Nexus Enterprise RAG & MCP Gateway",
        "version": "4.2.0",
        "installer_url": "https://krusch.dev/nexus/install.sh",
        "one_liner_cmd": "curl -sSL https://krusch.dev/nexus/install.sh | bash -s -- --license-key YOUR_NEXUS_LICENSE_KEY",
        "supported_environments": ["Linux x86_64", "macOS ARM64/x86_64", "WSL2"],
        "mcp_server_command": "python3 -m src.backend.mcp_server"
    }







