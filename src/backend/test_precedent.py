import sys
import asyncio
import src.backend.test_stubs
from src.backend.rag_engine import query_precedent_analysis
from src.backend.db import SessionLocal, Workspace

if __name__ == "__main__":
    db = SessionLocal()
    workspaces = db.query(Workspace).all()
    if not workspaces:
        print("No workspaces found in the database.")
        sys.exit(1)
    
    workspace_id = workspaces[0].id
    print(f"Using workspace {workspaces[0].name} (ID: {workspace_id})")

    query = "How did we historically structure auth middleware?"
    print(f"Testing precedent query: '{query}' on workspace {workspace_id}...")
    res = query_precedent_analysis(query, workspace_id)
    print("\n--- CLAIM ---")
    print(res.get("claim"))
    print("\n--- LOGIC ---")
    print(res.get("logic"))
    print("\n--- SOURCES ---")
    for s in res.get("sources", []):
        print(s)
