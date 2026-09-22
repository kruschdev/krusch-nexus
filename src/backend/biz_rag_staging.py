"""
KruschBiz Staging Module: Business-Specific RAG Tools & Helpers
==============================================================
These tools provide specialized business intelligence capabilities:
- SOP Action Checklist extraction
- Policy-compliant email draft generation
- Subject-Matter Expert (SME) routing

Per the architectural separation of concerns:
KruschNexus handles pure document ingestion & corpus indexing.
KruschBiz will serve as the dedicated business intelligence vertical for private
confidential corporate data (financials, SOPs, board decks, employee handbooks).
"""

import os
import json
from typing import Optional, Dict, Any, List

def get_sop_checklist(procedure_query: str, workspace_id: int = 1) -> str:
    """
    Extract structured phase-by-phase action items and checklists for any operational procedure.
    Staged for export to KruschBiz.
    """
    try:
        from src.backend.rag_engine import generate_sop_checklist
        return generate_sop_checklist(workspace_id, procedure_query)
    except Exception as e:
        return f"Error generating SOP checklist: {e}"


def get_email_draft_context(
    email_subject_or_content: str,
    client_or_topic: Optional[str] = None,
    workspace_name_or_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Retrieve and format company business context (SOP guidelines, past precedents, pricing rules)
    to draft a policy-compliant email. Staged for export to KruschBiz.
    """
    from src.backend.db import SessionLocal, Workspace
    from src.backend.rag_engine import smart_query, query_cross_workspace_graph

    db = SessionLocal()
    try:
        workspace_id = None
        if workspace_name_or_id:
            if str(workspace_name_or_id).isdigit():
                workspace_id = int(workspace_name_or_id)
            else:
                ws = db.query(Workspace).filter(Workspace.name.ilike(workspace_name_or_id)).first()
                if ws:
                    workspace_id = ws.id

        full_query = f"Email context & business policy for: {email_subject_or_content}"
        if client_or_topic:
            full_query += f" (Client/Topic: {client_or_topic})"

        if workspace_id:
            res = smart_query(full_query, workspace_id)
        else:
            res = query_cross_workspace_graph(full_query)

        response_text = res.get("response", str(res)) if isinstance(res, dict) else str(res)
        sources = res.get("sources", []) if isinstance(res, dict) else []

        formatted = (
            f"### 📧 Business Email Guidance & Context\n\n"
            f"**Query/Subject:** {email_subject_or_content}\n\n"
            f"**Institutional Context & Protocol:**\n{response_text}\n\n"
            f"**Key Documents Cited:**\n"
        )
        for src in sources:
            fn = src.get("filename") or src.get("file_name") or "Document"
            formatted += f"- 📄 {fn}\n"

        return {
            "status": "success",
            "email_guidance": formatted,
            "raw_response": response_text,
            "sources": sources
        }
    finally:
        db.close()


def find_expert_sme(topic_or_query: str) -> Dict[str, Any]:
    """
    Identify role owners, document authors, or SMEs for a topic. Staged for export to KruschBiz.
    """
    from src.backend.db import SessionLocal
    from src.backend.rag_engine import query_expert_finder

    db = SessionLocal()
    try:
        result = query_expert_finder(topic_or_query, db=db)
        return {"status": "success", "expert_analysis": result}
    finally:
        db.close()
