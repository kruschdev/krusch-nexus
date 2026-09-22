import sys
import asyncio
from src.backend.rag_engine import query_with_audit_trail
from src.backend.db import SessionLocal, Workspace

if __name__ == "__main__":
    db = SessionLocal()
    workspaces = db.query(Workspace).all()
    if not workspaces:
        print("No workspaces found in the database.")
        sys.exit(1)
    
    workspace_id = workspaces[0].id
    print(f"Using workspace {workspaces[0].name} (ID: {workspace_id})")

    query = "What is the architecture of the pocket lawyer platform?"
    print(f"Testing query: '{query}' on workspace {workspace_id}...")
    res = query_with_audit_trail(query, workspace_id)
    print("\n--- CLAIM ---")
    print(res.get("claim"))
    print("\n--- LOGIC ---")
    print(res.get("logic"))
    print("\n--- SOURCES ---")
    for s in res.get("sources", []):
        print(s)
