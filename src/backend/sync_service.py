import logging
import time
import json
from sqlalchemy.orm import Session
from llama_index.core import Document as LlamaDocument

from .db import Workspace, Document as DocModel, Employee
from .sync_provider import (
    LocalJsonDirectoryProvider,
    GoogleWorkspaceDirectoryProvider,
    JiraSyncProvider,
    ConfluenceSyncProvider,
    NotionSyncProvider,
    Microsoft365SyncProvider
)
from .rag_engine import index_documents

logger = logging.getLogger("nexus.sync_service")

def get_provider(provider_name: str):
    provider_name = provider_name.lower().strip()
    if provider_name in ["google", "google_workspace", "googleworkspace", "google-workspace"]:
        return GoogleWorkspaceDirectoryProvider()
    elif provider_name == "jira":
        return JiraSyncProvider()
    elif provider_name == "confluence":
        return ConfluenceSyncProvider()
    elif provider_name == "notion":
        return NotionSyncProvider()
    elif provider_name in ["m365", "microsoft", "azure"]:
        return Microsoft365SyncProvider()
    else:
        return LocalJsonDirectoryProvider()


def sync_directory_data(provider_name: str, db: Session) -> dict:
    """Fetches users from the provider and updates the employees table."""
    logger.info(f"Starting directory sync for provider: {provider_name}")
    try:
        provider = get_provider(provider_name)
        users = provider.fetch_users()
        
        synced_count = 0
        updated_count = 0
        
        for u in users:
            # Check if employee already exists by email or external_id
            emp = db.query(Employee).filter(
                (Employee.email == u["email"]) |
                ((Employee.provider == provider_name) & (Employee.external_id == u["external_id"]))
            ).first()
            
            if emp:
                emp.first_name = u.get("first_name", emp.first_name)
                emp.last_name = u.get("last_name", emp.last_name)
                emp.display_name = u.get("display_name", emp.display_name)
                emp.job_title = u.get("job_title", emp.job_title)
                emp.department = u.get("department", emp.department)
                emp.status = u.get("status", emp.status)
                emp.custom_metadata = json.dumps(u.get("custom_metadata", {}))
                updated_count += 1
            else:
                new_emp = Employee(
                    provider=provider_name,
                    external_id=u["external_id"],
                    email=u["email"],
                    first_name=u.get("first_name"),
                    last_name=u.get("last_name"),
                    display_name=u.get("display_name"),
                    job_title=u.get("job_title"),
                    department=u.get("department"),
                    status=u.get("status", "active"),
                    custom_metadata=json.dumps(u.get("custom_metadata", {}))
                )
                db.add(new_emp)
                synced_count += 1
                
        db.commit()
        logger.info(f"Directory sync complete. Created: {synced_count}, Updated: {updated_count}")
        return {
            "status": "success",
            "provider": provider_name,
            "created": synced_count,
            "updated": updated_count,
            "total_synced": len(users)
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Error during directory sync: {e}")
        return {"status": "error", "detail": str(e)}

def sync_documents_data(provider_name: str, workspace_id: int, db: Session) -> dict:
    """Fetches emails and files from the provider, saves them as local Documents, and indexes them."""
    logger.info(f"Starting document/email sync for provider: {provider_name} to workspace: {workspace_id}")
    try:
        # Verify workspace exists
        workspace = db.query(Workspace).filter(Workspace.id == workspace_id).first()
        if not workspace:
            return {"status": "error", "detail": f"Workspace {workspace_id} not found"}
            
        provider = get_provider(provider_name)
        
        # 1. Sync Emails
        emails = provider.fetch_emails()
        emails_synced = 0
        for email in emails:
            filename = f"email_{email['external_id']}.txt"
            
            # Check if this email is already imported
            doc = db.query(DocModel).filter(
                (DocModel.workspace_id == workspace_id) & (DocModel.filename == filename)
            ).first()
            
            if not doc:
                # Add document record to DB
                doc = DocModel(filename=filename, workspace_id=workspace_id)
                db.add(doc)
                db.commit()
                db.refresh(doc)
                
                # Create raw content format
                content = f"From: {email['sender']}\nSubject: {email['subject']}\nDate: {email['date']}\n\n{email['body']}"
                llama_doc = LlamaDocument(text=content)
                
                # Build indexing metadata
                metadata = {
                    "document_id": doc.id,
                    "workspace_id": workspace.id,
                    "workspace_name": workspace.name,
                    "filename": filename,
                    "timestamp": str(time.time()),
                    "type": "email",
                    "sender": email['sender']
                }
                
                # Trigger LlamaIndex Vector/Graph index
                index_documents([llama_doc], metadata, extract_graph=True)
                emails_synced += 1
                
        # 2. Sync Files (Drive Documents)
        files = provider.fetch_files()
        files_synced = 0
        for file in files:
            filename = file["filename"]
            
            # Check if this file is already imported
            doc = db.query(DocModel).filter(
                (DocModel.workspace_id == workspace_id) & (DocModel.filename == filename)
            ).first()
            
            if not doc:
                doc = DocModel(filename=filename, workspace_id=workspace_id)
                db.add(doc)
                db.commit()
                db.refresh(doc)
                
                llama_doc = LlamaDocument(text=file["content"])
                metadata = {
                    "document_id": doc.id,
                    "workspace_id": workspace.id,
                    "workspace_name": workspace.name,
                    "filename": filename,
                    "timestamp": str(time.time()),
                    "type": "file"
                }
                
                index_documents([llama_doc], metadata, extract_graph=True)
                files_synced += 1
                
        return {
            "status": "success",
            "provider": provider_name,
            "emails_synced": emails_synced,
            "files_synced": files_synced,
            "total_emails_fetched": len(emails),
            "total_files_fetched": len(files)
        }
    except Exception as e:
        logger.error(f"Error during document sync: {e}")
        return {"status": "error", "detail": str(e)}
