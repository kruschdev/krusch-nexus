"""
Autonomous Data Flywheel Crawler (nexus_crawler)
Runs on kruschserv (2080 Ti).
Crawls external data sources, extracts ideas using Gemma,
checks for novelty using BGE-large against krusch-nexus PostgreSQL,
and dispatches novel ideas to the DBOS Swarm queue on kruschdev.
"""

import os
import time
import json
import asyncio
import urllib.request
import xml.etree.ElementTree as ET
import uuid
from datetime import datetime, timezone

from .rag_engine import llm_fast, get_vector_store, audit_document_pair
from llama_index.core import VectorStoreIndex
from .db import SessionLocal, Workspace, Document as DbDocument, GraphNode, GraphEdge
from .swarm import get_dbos_conn

# Configuration
RSS_FEEDS = [
    "https://hnrss.org/frontpage",  # HackerNews Frontpage
    "https://lobste.rs/rss",        # Lobsters (Computing)
    "https://www.reddit.com/r/homelab/.rss", # Homelab
    "https://www.reddit.com/r/MachineLearning/.rss", # ML
    "https://www.reddit.com/r/LocalLLaMA/.rss", # Local LLMs
]
CRAWL_INTERVAL_SECONDS = int(os.getenv("CRAWL_INTERVAL", "3600"))

def fetch_rss_items(feed_url):
    """Fetch and parse RSS feed items."""
    try:
        req = urllib.request.Request(feed_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            xml_data = response.read()
        
        root = ET.fromstring(xml_data)
        items = []
        for item in root.findall('.//item')[:5]: # Top 5 items per run to avoid spam
            title = item.findtext('title', '')
            description = item.findtext('description', '')
            link = item.findtext('link', '')
            if title:
                items.append({'title': title, 'description': description, 'link': link})
        return items
    except Exception as e:
        print(f"[Flywheel] RSS Fetch Error for {feed_url}: {e}")
        return []

def extract_idea(item):
    """Use the conversational model (Gemma) to extract an actionable idea."""
    prompt = f"""
    You are an AI Scout analyzing data for a software engineering and infrastructure homelab swarm.
    Review the following article summary. If it contains a novel software engineering concept, architectural pattern, or useful technical idea, extract it into a concrete 'Idea Summary'. 
    If it is just news, politics, or irrelevant, reply with EXACTLY "NONE".
    
    Title: {item['title']}
    Description: {item['description']}
    """
    try:
        response = llm_fast.complete(prompt)
        res_str = str(response).strip()
        if res_str == "NONE" or not res_str:
            return None
        return res_str
    except Exception as e:
        print(f"[Flywheel] Extraction Error: {e}")
        return None

def check_novelty(idea_text):
    """Use BGE-large to check if the idea already exists in the nexus knowledge base."""
    try:
        vector_store = get_vector_store()
        index = VectorStoreIndex.from_vector_store(vector_store=vector_store)
        
        # We query the DB to see if there are extremely similar concepts.
        query_engine = index.as_query_engine(
            similarity_top_k=3,
            llm=llm_fast # Use fast model for evaluation
        )
        
        prompt = f"""
        Does our existing knowledge base cover this idea? 
        If our internal knowledge already implements this, answer "YES_EXISTS". 
        If this is a completely novel or unaddressed idea in our context, answer "NOVEL".
        Idea: {idea_text}
        """
        response = query_engine.query(prompt)
        res_str = str(response).strip().upper()
        
        if "NOVEL" in res_str:
            return True
        return False
    except Exception as e:
        print(f"[Flywheel] Novelty Check Error: {e}")
        return True # Default to novel if search fails so we don't drop ideas

def dispatch_to_swarm(item, idea_text):
    """Dispatch the novel idea as an intent job to the DBOS execution queue."""
    try:
        job_id = str(uuid.uuid4())
        thread_id = str(uuid.uuid4())
        
        payload = {
            "task": "explore_novel_idea",
            "instructions": f"We discovered a novel idea from external sources: {item['title']}. Idea Summary: {idea_text}. Please debate the architectural feasibility of implementing this in our homelab and synthesize a prototype plan.",
            "context": {
                "source_url": item['link'],
                "dispatched_by": "data_flywheel",
                "target_node": "kruschdev"
            }
        }
        
        with get_dbos_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agent_execution_queue (job_id, thread_id, job_type, payload, status, started_at_ms)
                    VALUES (%s, %s, %s, %s, 'pending', %s)
                    """,
                    [job_id, thread_id, 'intent', json.dumps(payload), int(datetime.now(timezone.utc).timestamp() * 1000)]
                )
            conn.commit()
        print(f"[Flywheel] 🚀 Dispatched novel idea to Swarm: {item['title']}")
    except Exception as e:
        print(f"[Flywheel] Dispatch Error: {e}")

def fetch_random_codebase_file():
    """Fetch a random code file from the kruschdb.blobs table (PG-Git snapshot)."""
    try:
        with get_dbos_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT b.file_path, b.content, r.name as project
                    FROM blobs b 
                    JOIN repositories r ON b.repository_id = r.id 
                    WHERE (b.file_path LIKE '%.js' OR b.file_path LIKE '%.py' OR b.file_path LIKE '%.ts')
                      AND b.content IS NOT NULL
                      AND b.file_path NOT LIKE '%node_modules%' 
                      AND b.file_path NOT LIKE '%.venv%' 
                      AND b.file_path NOT LIKE '%dist/%'
                      AND b.file_path NOT LIKE '%build/%'
                    ORDER BY RANDOM() LIMIT 1;
                """)
                row = cur.fetchone()
                if row:
                    file_path, content_bytes, project = row
                    if not content_bytes:
                        return None
                        
                    if isinstance(content_bytes, memoryview):
                        content_str = content_bytes.tobytes().decode('utf-8', errors='ignore')
                    elif isinstance(content_bytes, bytes):
                        content_str = content_bytes.decode('utf-8', errors='ignore')
                    else:
                        content_str = str(content_bytes)
                        
                    return {"file_path": file_path, "content": content_str, "project": project}
    except Exception as e:
        print(f"[Flywheel] Fetch Codebase Error: {e}")
    return None

def evaluate_code_standards(file_path, content_str, project):
    """Evaluate a code snippet against homelab standards using the LLM."""
    if not content_str or len(content_str) < 50:
        return "NONE"
    
    # Cap size to avoid blowing context
    content_chunk = content_str[:12000]
    
    prompt = f"""
    You are a Senior Staff Engineer evaluating our homelab codebase.
    Review the following file from project '{project}', path '{file_path}'.
    
    Our Homelab Coding Standards:
    1. Use ES Modules (import/export) instead of CommonJS (require/module.exports) for JavaScript/TypeScript.
    2. Keep functions under 40 lines; break monolithic functions down.
    3. Use @krusch/toolkit modules (e.g., llm, db, config) instead of redefining generic utilities.
    4. Remove dead code, console.log debug statements, or hardcoded credentials.
    5. Ensure proper JSDoc or Python docstrings for exported/public functions.
    
    Does this file violate these standards?
    If it looks mostly fine or is a third-party dependency, reply EXACTLY with the word "NONE".
    If it violates standards, extract the specific issue and provide a concise refactoring suggestion (under 3 sentences).
    
    File Content:
    {content_chunk}
    """
    try:
        response = llm_fast.complete(prompt)
        res_str = str(response).strip()
        if res_str == "NONE" or not res_str or "NONE" in res_str.upper()[:10]:
            return "NONE"
        return res_str
    except Exception as e:
        print(f"[Flywheel] Standards Evaluation Error: {e}")
        return "NONE"

def dispatch_code_audit(project, file_path, suggestion):
    """Dispatch a sandbox audit intent to refactor a file that violates standards."""
    try:
        job_id = str(uuid.uuid4())
        thread_id = str(uuid.uuid4())
        
        payload = {
            "project": project,
            "instructions": f"Audit {file_path}. The codebase crawler found: {suggestion}. Please refactor the file to meet homelab standards."
        }
        
        with get_dbos_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agent_execution_queue (job_id, thread_id, job_type, payload, status, started_at_ms)
                    VALUES (%s, %s, %s, %s, 'pending', %s)
                    """,
                    [job_id, thread_id, 'sandbox_audit', json.dumps(payload), int(datetime.now(timezone.utc).timestamp() * 1000)]
                )
            conn.commit()
        print(f"[Flywheel] 🛠️ Dispatched Sandbox Audit for {project}/{file_path}")
    except Exception as e:
        print(f"[Flywheel] Dispatch Audit Error: {e}")

async def codebase_crawler_loop():
    print("[Flywheel] Autonomous Codebase Crawler Started.")
    while True:
        try:
            code_file = fetch_random_codebase_file()
            if code_file:
                print(f"[Flywheel] Evaluating standards for {code_file['project']}/{code_file['file_path']}")
                suggestion = evaluate_code_standards(code_file['file_path'], code_file['content'], code_file['project'])
                if suggestion != "NONE":
                    dispatch_code_audit(code_file['project'], code_file['file_path'], suggestion)
                else:
                    print(f"[Flywheel] {code_file['file_path']} meets standards.")
        except Exception as e:
            print(f"[Flywheel] Codebase Crawler Error: {e}")
            
        # Sleep for 15 seconds between code audits to allow the GPU to breathe
        await asyncio.sleep(15)

async def rss_crawler_loop():
    print("[Flywheel] Autonomous RSS Crawler Started.")
    while True:
        try:
            for feed in RSS_FEEDS:
                print(f"[Flywheel] Crawling {feed}...")
                items = fetch_rss_items(feed)
                for item in items:
                    print(f"[Flywheel] Analyzing: {item['title']}")
                    idea = extract_idea(item)
                    if idea:
                        is_novel = check_novelty(idea)
                        if is_novel:
                            dispatch_to_swarm(item, idea)
                        else:
                            print(f"[Flywheel] Idea already exists in knowledge base: {item['title']}")
                    else:
                        print(f"[Flywheel] Not actionable/irrelevant: {item['title']}")
                    
                    # Pace the LLM requests
                    await asyncio.sleep(5)
        except Exception as e:
            print(f"[Flywheel] RSS Crawler Error: {e}")
            
        print(f"[Flywheel] RSS Crawler sleeping for {CRAWL_INTERVAL_SECONDS} seconds...")
        await asyncio.sleep(CRAWL_INTERVAL_SECONDS)


async def coordination_auditor_loop():
    print("[Flywheel] Autonomous Coordination Auditor Started.")
    while True:
        try:
            db = SessionLocal()
            workspaces = db.query(Workspace).all()
            for ws in workspaces:
                docs = db.query(DbDocument).filter(DbDocument.workspace_id == ws.id).all()
                if len(docs) < 2:
                    continue
                
                for i in range(len(docs)):
                    for j in range(i + 1, len(docs)):
                        doc_a = docs[i]
                        doc_b = docs[j]
                        
                        print(f"[Flywheel] Auditing doc pair: {doc_a.filename} vs {doc_b.filename} in Workspace '{ws.name}'...")
                        conflict_id = audit_document_pair(doc_a.id, doc_b.id, ws.id)
                        if conflict_id:
                            print(f"[Flywheel] ⚠️ Coordination conflict detected and saved to graph: Node ID {conflict_id}")
                        else:
                            print(f"[Flywheel] Audited: No conflicts found between {doc_a.filename} and {doc_b.filename}")
                        
                        # Pace the LLM requests
                        await asyncio.sleep(10)
                        
            db.close()
        except Exception as e:
            print(f"[Flywheel] Coordination Auditor Loop Error: {e}")
            
        # Run every 5 minutes
        print("[Flywheel] Coordination Auditor sleeping for 300 seconds...")
        await asyncio.sleep(300)


async def nightly_memory_consolidation_loop():
    print("[Flywheel] Nightly Memory Consolidation daemon started.")
    import json
    while True:
        try:
            db = SessionLocal()
            print("[Flywheel] Running memory consolidation and tagging sweep...")
            workspaces = db.query(Workspace).all()
            for ws in workspaces:
                active_conflicts = db.query(GraphNode).filter(
                    GraphNode.entity_type == "Conflict",
                    GraphNode.workspace_id == ws.id,
                    GraphNode.status == "active"
                ).all()
                
                if len(active_conflicts) < 2:
                    continue
                    
                conflicts_data = []
                for c in active_conflicts:
                    claim = ""
                    if c.custom_metadata:
                        try:
                            meta = json.loads(c.custom_metadata)
                            claim = meta.get("claim", "")
                        except Exception:
                            pass
                    conflicts_data.append(f"- Conflict '{c.entity_name}': {claim}")
                
                conflicts_str = "\n".join(conflicts_data)
                
                prompt = f"""
                You are the Company Brain's consolidation engine. Your task is to analyze the following active coordination conflicts and consolidate recurring patterns or duplicate issues into single high-level Coordination Invariants/Precedents.
                
                Active Conflicts:
                {conflicts_str}
                
                Identify if there are conflicts that share a root cause or cover similar departmental policies (e.g. data retention mismatches, API contracts).
                If you find a pattern, return a JSON object with:
                {{
                    "consolidate": true,
                    "title": "A high-level title for the consolidated precedent/invariant",
                    "invariant_rule": "A clear, actionable rule/precedent that resolves or prevents this conflict in the future",
                    "consolidated_node_names": ["List of exact titles of the conflicts that are consolidated by this rule"]
                }}
                If no consolidation is needed, return "NONE".
                """
                response = llm_fast.complete(prompt)
                res_str = str(response).strip()
                
                if "NONE" in res_str.upper()[:10]:
                    continue
                    
                if res_str.startswith("```json"):
                    res_str = res_str[7:-3].strip()
                elif res_str.startswith("```"):
                    res_str = res_str[3:-3].strip()
                    
                try:
                    data = json.loads(res_str)
                except Exception:
                    continue
                    
                if not data.get("consolidate"):
                    continue
                    
                invariant_title = data.get("title", "Consolidated Precedent")
                invariant_rule = data.get("invariant_rule", "")
                consolidated_names = data.get("consolidated_node_names", [])
                
                invariant_node = db.query(GraphNode).filter(
                    GraphNode.entity_name == invariant_title,
                    GraphNode.entity_type == "Invariant",
                    GraphNode.workspace_id == ws.id
                ).first()
                
                if not invariant_node:
                    invariant_node = GraphNode(
                        entity_name=invariant_title[:255],
                        entity_type="Invariant",
                        workspace_id=ws.id,
                        status="active",
                        custom_metadata=json.dumps({
                            "invariant_rule": invariant_rule,
                            "created_by": "memory_consolidator"
                        })
                    )
                    db.add(invariant_node)
                    db.flush()
                else:
                    invariant_node.status = "active"
                    invariant_node.custom_metadata = json.dumps({
                        "invariant_rule": invariant_rule,
                        "created_by": "memory_consolidator"
                    })
                    db.flush()
                
                for name in consolidated_names:
                    c_node = db.query(GraphNode).filter(
                        GraphNode.entity_name == name,
                        GraphNode.entity_type == "Conflict",
                        GraphNode.workspace_id == ws.id
                    ).first()
                    if c_node:
                        c_node.status = "archived"
                        
                        edge = db.query(GraphEdge).filter(
                            GraphEdge.source_node_id == invariant_node.id,
                            GraphEdge.target_node_id == c_node.id,
                            GraphEdge.relationship_type == "resolves",
                            GraphEdge.workspace_id == ws.id
                        ).first()
                        if not edge:
                            edge = GraphEdge(
                                source_node_id=invariant_node.id,
                                target_node_id=c_node.id,
                                relationship_type="resolves",
                                workspace_id=ws.id,
                                status="active"
                            )
                            db.add(edge)
                            
                db.commit()
                print(f"[Flywheel] Successfully consolidated {len(consolidated_names)} conflicts into Invariant '{invariant_title}'")
                
            db.close()
        except Exception as e:
            print(f"[Flywheel] Error in memory consolidation sweep: {e}")
            
        print("[Flywheel] Memory consolidation daemon sleeping for 24 hours...")
        await asyncio.sleep(86400)


async def main():
    print("[Flywheel] Starting all autonomous agents...")
    await asyncio.gather(
        rss_crawler_loop(),
        codebase_crawler_loop(),
        coordination_auditor_loop(),
        nightly_memory_consolidation_loop()
    )

if __name__ == "__main__":
    asyncio.run(main())
