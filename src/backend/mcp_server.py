import os
import json
import httpx
import subprocess
from mcp.server.fastmcp import FastMCP
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from src.backend.swarm import get_swarm_jobs, get_swarm_stats, get_jean_swarm_stats, get_human_reviews, get_debate_thread, update_review_status, execute_sandbox_test

DATABASE_URL = os.getenv("DBOS_DATABASE_URL", "postgresql://openclaw:openclaw_password@10.0.0.85:5434/kruschdb")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "openrouter").lower()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_EMBED_MODEL = os.getenv("OPENROUTER_EMBED_MODEL", "baai/bge-large-en-v1.5")
OLLAMA_EMBED_HOST = os.getenv("OLLAMA_EMBED_HOST", "http://10.0.0.85:11434")
EMBED_MODEL = "bge-large"
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

# Initialize fastMCP
mcp = FastMCP("KruschRetrievalMCP")

# DB connection
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Lazy loading for the reranker so it doesn't block startup
_reranker = None

def get_reranker():
    global _reranker
    if _reranker is None:
        try:
            from sentence_transformers import CrossEncoder
            _reranker = CrossEncoder(RERANKER_MODEL, max_length=512)
        except Exception as e:
            print(f"Warning: CrossEncoder reranker unavailable ({e}). Using cosine/RRF rankings.")
            return None
    return _reranker

def get_embedding(query: str):
    """Call OpenRouter or local Ollama for BGE-Large embedding."""
    if EMBEDDING_PROVIDER == "openrouter" and OPENROUTER_API_KEY:
        try:
            resp = httpx.post(
                "https://openrouter.ai/api/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "HTTP-Referer": "https://github.com/krusch-nexus",
                    "X-Title": "Krusch-Nexus Embedding Engine",
                    "Content-Type": "application/json"
                },
                json={"model": OPENROUTER_EMBED_MODEL, "input": query},
                timeout=30.0
            )
            resp.raise_for_status()
            data = resp.json()
            if "data" in data and len(data["data"]) > 0:
                return data["data"][0]["embedding"]
        except Exception as e:
            print(f"OpenRouter embedding error for '{query[:20]}': {e}. Falling back to Ollama...")

    # Local Ollama fallback
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
        print(f"Ollama embedding error: {e}")
    return None

@mcp.tool()
def krusch_context_search_code(query: str, project: str = None) -> str:
    """
    Search the Kruschdb knowledge base for code or documents using semantic vector search and cross-encoder reranking.
    """
    # 1. Embed query
    vector = get_embedding(query)
    if not vector:
        return json.dumps({"error": "Failed to embed query"})
    
    vec_str = '[' + ','.join(str(v) for v in vector) + ']'
    
    # 2. HNSW Search (Top 50)
    db = SessionLocal()
    try:
        if project:
            sql = text("""
                SELECT file_name, file_path, summary, content,
                       1 - (embedding <=> CAST(:vec AS vector)) AS cosine_sim
                FROM blobs
                WHERE embedding IS NOT NULL AND repository_id IN (SELECT id FROM repositories WHERE name = :project)
                ORDER BY embedding <=> CAST(:vec AS vector)
                LIMIT 50
            """)
            params = {"vec": vec_str, "project": project}
        else:
            sql = text("""
                SELECT file_name, file_path, summary, content,
                       1 - (embedding <=> CAST(:vec AS vector)) AS cosine_sim
                FROM blobs
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:vec AS vector)
                LIMIT 50
            """)
            params = {"vec": vec_str}
            
        rows = db.execute(sql, params).fetchall()
        
        if not rows:
            return json.dumps({"results": [], "message": "No results found."})
            
        # 3. Rerank top 50
        reranker = get_reranker()
        pairs = []
        doc_map = []
        for row in rows:
            content_str = ""
            if row.content:
                if isinstance(row.content, (bytes, memoryview)):
                    content_str = bytes(row.content).decode('utf-8', errors='replace')
                else:
                    content_str = str(row.content)
            
            # Text used for reranking (combine summary + snippet)
            # Reranker has 512 token context limit (~2000 chars)
            snippet = content_str[:1000] if content_str else ""
            summary_part = f"Summary: {row.summary}\n" if row.summary and row.summary != '[SKIP]' else ""
            text_for_rerank = f"{row.file_name or row.file_path}\n{summary_part}{snippet}"
            
            pairs.append([query, text_for_rerank])
            doc_map.append({
                "file_name": row.file_name,
                "file_path": row.file_path,
                "summary": row.summary,
                "content_preview": content_str[:2000] if content_str else ""
            })
            
        scores = reranker.predict(pairs)
        
        # Combine and sort by score
        scored_docs = list(zip(doc_map, scores))
        scored_docs.sort(key=lambda x: x[1], reverse=True)
        
        # 4. Return Top 5
        top_5 = scored_docs[:5]
        
        results = []
        for doc, score in top_5:
            results.append({
                "file_path": doc["file_path"],
                "file_name": doc["file_name"],
                "summary": doc["summary"],
                "content_preview": doc["content_preview"],
                "rerank_score": float(score)
            })
            
        return json.dumps({"results": results})
        
    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        db.close()


# ─── Episodic Memory Tools (ide_agent_memory) ──────────────────

@mcp.tool()
def krusch_context_search_memory(category: str, query: str, project: str = None, limit: int = 5) -> str:
    """
    Semantic search over episodic memory (lessons, bugs, outcomes, priorities, activity).
    Uses bge-large embeddings + cosine similarity on kruschdb.ide_agent_memory.
    """
    vector = get_embedding(query)
    if not vector:
        return json.dumps({"error": "Failed to embed query"})

    vec_str = '[' + ','.join(str(v) for v in vector) + ']'
    db = SessionLocal()
    try:
        if project:
            sql = text("""
                SELECT id, content, tags, project, created_at,
                       1 - (embedding <=> CAST(:vec AS vector)) AS similarity
                FROM ide_agent_memory
                WHERE category = :category AND project = :project AND embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:vec AS vector)
                LIMIT :limit
            """)
            params = {"vec": vec_str, "category": category, "project": project, "limit": limit}
        else:
            sql = text("""
                SELECT id, content, tags, project, created_at,
                       1 - (embedding <=> CAST(:vec AS vector)) AS similarity
                FROM ide_agent_memory
                WHERE category = :category AND embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:vec AS vector)
                LIMIT :limit
            """)
            params = {"vec": vec_str, "category": category, "limit": limit}

        rows = db.execute(sql, params).fetchall()
        results = []
        for row in rows:
            results.append({
                "id": str(row.id),
                "content": row.content,
                "tags": row.tags,
                "project": row.project,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "similarity": round(float(row.similarity), 4)
            })
        return json.dumps({"results": results, "count": len(results)})
    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        db.close()


@mcp.tool()
def krusch_context_write_state(category: str, content: str, author_id: str = "jean", project: str = None, tags: str = None) -> str:
    """
    Write a new episodic memory state (lesson, bug, outcome, priority, activity).
    Embeds the content with bge-large and stores in kruschdb.ide_agent_memory.
    Valid categories: priorities, bugs, outcomes, lessons, activity.
    """
    valid_cats = {"priorities", "bugs", "outcomes", "lessons", "activity"}
    if category not in valid_cats:
        return json.dumps({"error": f"Invalid category '{category}'. Valid: {valid_cats}"})

    vector = get_embedding(content)
    if not vector:
        return json.dumps({"error": "Failed to embed content"})

    vec_str = '[' + ','.join(str(v) for v in vector) + ']'
    tag_str = tags if tags else f"author:{author_id}"

    db = SessionLocal()
    try:
        sql = text("""
            INSERT INTO ide_agent_memory (id, category, content, project, tags, embedding, created_at)
            VALUES (gen_random_uuid(), :category, :content, :project, :tags, CAST(:vec AS vector), NOW())
            RETURNING id
        """)
        result = db.execute(sql, {
            "category": category,
            "content": content,
            "project": project,
            "tags": tag_str,
            "vec": vec_str
        })
        db.commit()
        new_id = str(result.fetchone()[0])
        return json.dumps({"status": "success", "id": new_id, "message": f"Memory saved to '{category}' (project: {project or 'global'})"})
    except Exception as e:
        db.rollback()
        return json.dumps({"error": str(e)})
    finally:
        db.close()


@mcp.tool()
def krusch_context_list_memories(category: str, project: str = None, limit: int = 10) -> str:
    """
    List recent memories chronologically (newest first). No embedding needed.
    """
    db = SessionLocal()
    try:
        if project:
            sql = text("""
                SELECT id, content, tags, created_at
                FROM ide_agent_memory
                WHERE category = :category AND project = :project
                ORDER BY created_at DESC LIMIT :limit
            """)
            params = {"category": category, "project": project, "limit": limit}
        else:
            sql = text("""
                SELECT id, content, tags, project, created_at
                FROM ide_agent_memory
                WHERE category = :category
                ORDER BY created_at DESC LIMIT :limit
            """)
            params = {"category": category, "limit": limit}

        rows = db.execute(sql, params).fetchall()
        results = []
        for row in rows:
            entry = {
                "id": str(row.id),
                "content": row.content,
                "tags": row.tags,
                "created_at": row.created_at.isoformat() if row.created_at else None
            }
            if hasattr(row, 'project'):
                entry["project"] = row.project
            results.append(entry)
        return json.dumps({"results": results, "count": len(results)})
    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        db.close()

@mcp.tool()
def krusch_context_get_swarm_stats() -> str:
    """Get aggregate statistics for the DBOS agent execution queue."""
    return json.dumps(get_swarm_stats())

@mcp.tool()
def krusch_context_get_jean_swarm_stats() -> str:
    """Get swarm stats broken down by job_type for Jean SRE. Separates intent jobs from debate activity."""
    return json.dumps(get_jean_swarm_stats())

@mcp.tool()
def krusch_context_get_swarm_jobs(status: str = "pending", limit: int = 10) -> str:
    """Fetch jobs from the DBOS agent execution queue (e.g., status='pending', 'running', or 'completed')."""
    return json.dumps(get_swarm_jobs(status=status, limit=limit))

@mcp.tool()
def krusch_context_get_human_reviews(limit: int = 20) -> str:
    """Fetch pending human review items (synthesized prototype plans)."""
    return json.dumps(get_human_reviews(limit=limit))

@mcp.tool()
def krusch_context_get_debate_thread(thread_id: str) -> str:
    """Fetch all jobs in a debate thread to show the full Idea -> Critique -> Synthesis chain."""
    return json.dumps(get_debate_thread(thread_id))

@mcp.tool()
def krusch_context_update_review_status(job_id: str, new_status: str) -> str:
    """Mark a human review as 'approved' or 'rejected'. If approved, the swarm execution job will be dispatched."""
    success = update_review_status(job_id, new_status)
    if success:
        return json.dumps({"status": "success", "message": f"Job {job_id} successfully marked as {new_status}."})
    else:
        return json.dumps({"status": "error", "message": f"Failed to update job {job_id}. Verify the ID is correct and it is a pending review."})

@mcp.tool()
def krusch_context_execute_on_sandbox(command: str, cwd: str = "~") -> str:
    """Submit a shell command to the DBOS queue to be executed on the kruschgame sandbox."""
    return json.dumps(execute_sandbox_test(command, cwd))

# --- Pocket Lawyer Business Pro MCP Tools ---
from src.backend.pocketlawyer.business_legal_tools import BusinessLegalTools
from src.backend.pocketlawyer.business_profile_manager import save_profile, load_profile

@mcp.tool()
def krusch_business_review_contract(contract_text: str, contract_type: str = "service_agreement") -> str:
    """
    Analyze a business contract, identify high/medium risk areas, and suggest modifications.
    """
    try:
        tools = BusinessLegalTools()
        res = tools.contract_reviewer.analyze(contract_text, contract_type)
        return json.dumps(res)
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
def krusch_business_assess_risk(employee_count: int = 0, annual_revenue: float = 0.0,
                               handles_data: bool = False, has_contracts: bool = False,
                               industry: str = "general") -> str:
    """
    Assess overall legal risk exposure for a business based on its size, revenue, industry, and operations.
    """
    try:
        tools = BusinessLegalTools()
        profile = {
            'employee_count': employee_count,
            'annual_revenue': annual_revenue,
            'handles_data': handles_data,
            'has_contracts': has_contracts,
            'industry': industry
        }
        res = tools.risk_assessor.assess_business(profile)
        return json.dumps(res)
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
def krusch_business_demand_letter(creditor: str, debtor: str, amount: float,
                                  invoice_date: str, description: str) -> str:
    """
    Generate a formal legal demand letter to collect unpaid business debt.
    """
    try:
        tools = BusinessLegalTools()
        res = tools.dispute_strategist.demand_letter_template(
            creditor, debtor, amount, invoice_date, description
        )
        return res
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
def krusch_business_new_hire_checklist(employee_type: str = "non_exempt") -> str:
    """
    Generate a comprehensive onboarding and compliance checklist for a new employee (exempt or non_exempt).
    """
    try:
        tools = BusinessLegalTools()
        res = tools.employment_advisor.new_hire_checklist(employee_type)
        return json.dumps(res)
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
def krusch_business_compliance_calendar(entity_type: str = "llc", inception_date: str = "2026-01-01") -> str:
    """
    Generate LLC/Corp Statement of Information filing and compliance calendar based on inception date.
    """
    try:
        tools = BusinessLegalTools()
        res = tools.entity_manager.compliance_calendar(entity_type, inception_date)
        return json.dumps(res)
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
def krusch_business_collection_strategy(amount: float, agreement_type: str, delinquency_days: int) -> str:
    """
    Evaluate collection strategy for outstanding customer/client invoices.
    """
    try:
        tools = BusinessLegalTools()
        res = tools.dispute_strategist.collection_strategy(amount, agreement_type, delinquency_days)
        return json.dumps(res)
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
def krusch_business_get_profile(user_id: int = 1) -> str:
    """
    Load the saved business onboarding profile.
    """
    try:
        profile = load_profile(user_id)
        if not profile:
            return json.dumps({"status": "empty", "profile": {}})
        return json.dumps({"status": "success", "profile": profile})
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
def krusch_business_save_profile(company_name: str, entity_type: str, ein: str, state: str,
                                 employee_count: int = 0, industry: str = "general",
                                 services: str = "", compliance_licenses: str = "",
                                 goals: str = "", notes: str = "", user_id: int = 1) -> str:
    """
    Save or update the business onboarding profile.
    """
    try:
        profile_dict = {
            "company_name": company_name,
            "entity_type": entity_type,
            "ein": ein,
            "state": state,
            "employee_count": employee_count,
            "industry": industry,
            "services": services,
            "compliance_licenses": compliance_licenses,
            "goals": goals,
            "notes": notes
        }
        res = save_profile(user_id, profile_dict)
        return json.dumps(res)
    except Exception as e:
        return json.dumps({"error": str(e)})

# --- Business RAG & Institutional Knowledge MCP Tools ---
from typing import Optional, List

@mcp.tool()
def nexus_list_workspaces() -> str:
    """
    List all institutional knowledge workspaces (e.g. General, Engineering, HR, Financials, Repos).
    """
    try:
        from src.backend.db import SessionLocal as NexusSessionLocal, Workspace
        db = NexusSessionLocal()
        try:
            workspaces = db.query(Workspace).all()
            results = [{"id": w.id, "name": w.name, "description": w.description} for w in workspaces]
            return json.dumps({"workspaces": results, "count": len(results)})
        finally:
            db.close()
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
def nexus_list_documents(workspace_name_or_id: Optional[str] = None, limit: int = 20) -> str:
    """
    List ingested company documents (SOPs, financial reports, emails, policies) in a workspace or across all workspaces.
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
                "created_at": str(getattr(d, "created_at", getattr(d, "uploaded_at", "")))
            } for d in docs]
            return json.dumps({"documents": results, "count": len(results)})
        finally:
            db.close()
    except Exception as e:
        return json.dumps({"error": str(e)})

def check_and_increment_query_quota(db, workspace_id: Optional[int] = None):
    """Enforces monthly query quota for subscription tiers."""
    if not workspace_id:
        return
    from src.backend.db import Workspace
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if ws:
        used = getattr(ws, "queries_used", 0) or 0
        limit = getattr(ws, "query_limit_monthly", 100) or 100
        tier = getattr(ws, "subscription_tier", "free") or "free"
        
        # Check if workspace user email is verified
        from src.backend.db import User
        user = db.query(User).filter(User.username == ws.name.replace("_workspace", "")).first()
        if user and not getattr(user, "email_verified", True) and used >= 50:
            raise ValueError("Unverified trial email: Sandbox query limit (50 queries) reached. Please verify your email to unlock your full tier quota.")

        if used >= limit:
            raise ValueError(f"Monthly query limit ({limit}) reached for '{tier}' subscription tier. Upgrade to Pro for 25,000 queries/mo.")
        ws.queries_used = used + 1
        db.commit()

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
    generates 1024d local embeddings, and stores structural chunks with HNSW & tsvector indexes.
    
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
def nexus_query_business_knowledge(query: str, workspace_name_or_id: Optional[str] = None, include_graph_context: bool = True) -> str:
    """
    Query corpus knowledge using hybrid vector retrieval + full-text RRF with exact citation footnotes.
    (Staged for KruschBiz; routes to hybrid search in KruschNexus).
    """
    try:
        from src.backend.db import SessionLocal as NexusSessionLocal, Workspace
        from src.backend.rag_engine import smart_query, query_cross_workspace_graph, retrieve_hybrid_document_chunks

        db = NexusSessionLocal()
        try:
            workspace_id = None
            if workspace_name_or_id:
                if str(workspace_name_or_id).isdigit():
                    workspace_id = int(workspace_name_or_id)
                else:
                    ws = db.query(Workspace).filter(Workspace.name.ilike(workspace_name_or_id)).first()
                    if ws:
                        workspace_id = ws.id

            check_and_increment_query_quota(db, workspace_id)

            # Query hybrid chunks directly for grounded citation
            chunks = retrieve_hybrid_document_chunks(query, workspace_id=workspace_id, limit=5, db=db)
            if chunks:
                sources = [
                    {
                        "filename": c.filename,
                        "page": c.page_number,
                        "header": c.header,
                        "citation": f"[{c.filename}, p. {c.page_number}{', § ' + c.header if c.header else ''}]",
                        "content_snippet": c.content[:300]
                    }
                    for c in chunks
                ]
                return json.dumps({
                    "status": "success",
                    "query": query,
                    "top_citation": sources[0]["citation"] if sources else None,
                    "sources": sources
                }, indent=2)

            if workspace_id:
                result = smart_query(query, workspace_id)
            else:
                result = query_cross_workspace_graph(query)

            return json.dumps({
                "status": "success",
                "query": query,
                "response": result.get("response", str(result)) if isinstance(result, dict) else str(result),
                "sources": result.get("sources", []) if isinstance(result, dict) else []
            })
        finally:
            db.close()
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
def nexus_get_email_draft_context(email_subject_or_content: str, client_or_topic: Optional[str] = None, workspace_name_or_id: Optional[str] = None) -> str:
    """
    [Staged for KruschBiz] Retrieve and format business guidelines, precedents, and rules for email drafting.
    """
    try:
        from src.backend.biz_rag_staging import get_email_draft_context
        res = get_email_draft_context(email_subject_or_content, client_or_topic, workspace_name_or_id)
        return json.dumps(res)
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
def nexus_ingest_business_document(filename: str, content: str, workspace_name: str = "General", category: str = "SOP") -> str:
    """
    Ingest a document payload (SOP, memo, email) directly into the corpus via MCP.
    """
    try:
        from src.backend.client import NexusIngestClient
        client = NexusIngestClient()
        report = client.ingest_text(content=content, filename=filename, workspace=workspace_name, doc_type=category.lower())
        return json.dumps(report, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
def nexus_get_sop_checklist(procedure_query: str, workspace_name_or_id: Optional[str] = None) -> str:
    """
    [Staged for KruschBiz] Extract structured action items and checklists for an operational procedure.
    """
    try:
        from src.backend.biz_rag_staging import get_sop_checklist
        ws_id = int(workspace_name_or_id) if str(workspace_name_or_id).isdigit() else 1
        checklist = get_sop_checklist(procedure_query, workspace_id=ws_id)
        return json.dumps({"status": "success", "checklist": checklist})
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
def nexus_find_expert(topic_or_query: str) -> str:
    """
    [Staged for KruschBiz] Find internal team subject-matter experts (SMEs) or document authors.
    """
    try:
        from src.backend.biz_rag_staging import find_expert_sme
        res = find_expert_sme(topic_or_query)
        return json.dumps(res)
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
def nexus_sync_google_workspace(workspace_name: str = "Google Workspace", sync_type: str = "all") -> str:
    """
    Synchronize files, Google Docs, Sheets, and Gmail threads from Google Workspace into a target knowledge workspace.
    """
    try:
        from src.backend.sync_provider import GoogleWorkspaceDirectoryProvider
        provider = GoogleWorkspaceDirectoryProvider()
        
        docs = provider.fetch_documents() if sync_type in ["all", "docs", "drive"] else []
        emails = provider.fetch_emails() if sync_type in ["all", "emails", "gmail"] else []
        
        return json.dumps({
            "status": "success",
            "workspace_name": workspace_name,
            "synced_documents_count": len(docs),
            "synced_emails_count": len(emails),
            "documents": [d.get("filename") for d in docs],
            "emails": [e.get("subject") for e in emails]
        })
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
def nexus_classify_document(doc_id: int, classification_level: str, allowed_roles: str = "all") -> str:
    """
    Set security classification level ('public', 'internal', 'confidential', 'management_only') and allowed role access for a document.
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
    Flag a document as sensitive or suspicious for management review and security audit.
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

if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8002"))

    if transport == "sse":
        print(f"Starting Krusch-Nexus MCP Server in SSE mode on {host}:{port}...", flush=True)
        mcp.run(transport="sse", host=host, port=port)
    else:
        mcp.run(transport="stdio")

