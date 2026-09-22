#!/usr/bin/env python3
"""
Metadata Backfill Script for Krusch-Nexus
Runs through existing embeddings in `data_workspace_documents_vectors_bge` and
updates them with new Risk Analysis and Component Type metadata.
"""

import os
import json
import time
import httpx
import random
import traceback
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from urllib.parse import urlparse
import concurrent.futures

# Configuration
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://krusch:kruschpassword@localhost:5432/krusch_oss_db")
OLLAMA_FAST_MODEL = os.getenv("OLLAMA_FAST_MODEL", "qwen2.5-coder:7b")
OLLAMA_TAG_MODEL = os.getenv("OLLAMA_TAG_MODEL", "qwen2.5-coder:14b")

OLLAMA_HOSTS = [
    "http://10.0.0.85:11434",   # kruschserv (RTX 3060)
    "http://10.0.0.144:11434",  # kruschdev (RTX 2080 Ti + RTX 3060)
    "http://10.0.0.19:11434"    # kruschgame (RTX 3050)
]

def get_ollama_host():
    return random.choice(OLLAMA_HOSTS)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

DOC_PROMPT = "Analyze the following document text and categorize it. Extract 'Industry' (e.g., Finance, Tech, Healthcare), 'Risk Level' (Low, Medium, High), 'Document Type' (e.g., Contract, Policy, Technical Spec), 'Sensitivity' (e.g., Public, Internal, Confidential), and 'Compliance Flags' (e.g., GDPR, SOC2, HIPAA) as an array of strings. Return ONLY JSON matching this schema: {{\"industry\": \"...\", \"risk_level\": \"...\", \"document_type\": \"...\", \"sensitivity\": \"...\", \"compliance_flags\": [\"...\"]}}.\n\nText: {text}"

CODE_PROMPT = """You are a code librarian. Given a source code file, output a JSON object with:
- "summary": A single sentence (max 120 chars) describing what this file does.
- "tags": An array of 3-5 lowercase tags categorizing the file.
- "security_critical": A boolean indicating if this file handles authentication, cryptography, passwords, or PII.
- "component_type": A string categorizing the component (e.g., "Frontend", "API", "Database Schema", "CI/CD Workflow", "Config", "Script").

Rules:
- Summary must be specific about the technology, pattern, or feature.
- Output ONLY valid JSON, no markdown fences.

Example: {{"summary":"Express middleware for JWT auth with RBAC","tags":["auth","jwt","middleware","express"],"security_critical":true,"component_type":"API"}}

File: {filename}

{text}"""

def process_document(vector_id, text_content, current_metadata):
    try:
        sample = text_content[:2000]
        prompt = DOC_PROMPT.format(text=sample)
        
        resp = httpx.post(
            f"{get_ollama_host()}/api/generate",
            json={"model": OLLAMA_FAST_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.1}},
            timeout=120.0
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()
        
        if raw.startswith("```json"): raw = raw[7:-3].strip()
        elif raw.startswith("```"): raw = raw[3:-3].strip()
        
        data = json.loads(raw)
        updates = {}
        for key in ["industry", "risk_level", "document_type", "sensitivity"]:
            if key in data: updates[key] = str(data[key])
        if "compliance_flags" in data and isinstance(data["compliance_flags"], list):
            updates["compliance_flags"] = [str(f) for f in data["compliance_flags"]]
            
        return vector_id, updates, None
    except Exception as e:
        return vector_id, None, traceback.format_exc()

def process_code(vector_id, text_content, filename, current_metadata):
    try:
        sample = text_content[:4000]
        prompt = CODE_PROMPT.format(filename=filename, text=sample)
        
        resp = httpx.post(
            f"{get_ollama_host()}/api/generate",
            json={"model": OLLAMA_TAG_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.1}},
            timeout=120.0
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()
        
        if raw.startswith("```json"): raw = raw[7:-3].strip()
        elif raw.startswith("```"): raw = raw[3:-3].strip()
        
        last_brace = raw.rfind('}')
        if last_brace > 0: raw = raw[:last_brace + 1]
        
        data = json.loads(raw)
        updates = {
            "ai_summary": str(data.get("summary", ""))[:200],
            "ai_tags": ", ".join([str(t).lower()[:30] for t in data.get("tags", [])[:8]]),
            "security_critical": bool(data.get("security_critical", False)),
            "component_type": str(data.get("component_type", "Unknown"))[:50]
        }
        return vector_id, updates, None
    except Exception as e:
        return vector_id, None, traceback.format_exc()

def process_row(row):
    vector_id = row[0]
    text_content = row[1]
    metadata = row[2]
    
    if "repo_url" in metadata:
        return process_code(vector_id, text_content, metadata.get("filename", "unknown"), metadata)
    else:
        return process_document(vector_id, text_content, metadata)

def main():
    print("Starting Metadata Backfill...")
    db = SessionLocal()
    
    # Find rows missing the new metadata keys
    query = text("""
        SELECT id, text, metadata_ 
        FROM data_workspace_documents_vectors_bge 
        WHERE (metadata_->>'repo_url' IS NOT NULL AND metadata_->>'component_type' IS NULL)
           OR (metadata_->>'repo_url' IS NULL AND metadata_->>'document_type' IS NULL)
    """)
    rows = db.execute(query).fetchall()
    print(f"Found {len(rows)} records needing backfill.")
    
    if not rows:
        print("All records up to date!")
        return

    success = 0
    failed = 0
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = {executor.submit(process_row, row): row for row in rows}
        
        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            vid, updates, err = future.result()
            
            if updates:
                # Update the database
                try:
                    update_query = text("""
                        UPDATE data_workspace_documents_vectors_bge 
                        SET metadata_ = CAST(CAST(metadata_ AS jsonb) || CAST(:updates AS jsonb) AS json)
                        WHERE id = :id
                    """)
                    db.execute(update_query, {"updates": json.dumps(updates), "id": vid})
                    db.commit()
                    success += 1
                except Exception as db_err:
                    db.rollback()
                    print(f"DB Update failed for ID {vid}: {db_err}")
                    failed += 1
            else:
                print(f"Row {vid} failed: {err}")
                failed += 1
                
            if (i + 1) % 50 == 0:
                print(f"Processed {i+1}/{len(rows)} (Success: {success}, Failed: {failed})")
                
    print(f"\nBackfill Complete! Updated: {success}, Failed: {failed}")

if __name__ == "__main__":
    main()
