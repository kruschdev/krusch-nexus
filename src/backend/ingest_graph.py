"""
LangGraph-based orchestration for Repo Ingestion.
"""
from typing import TypedDict, List, Optional, Dict, Any
from langgraph.graph import StateGraph, START, END
import concurrent.futures
import time

# Import the procedural components from repo_ingest
from .repo_ingest import (
    clone_repo,
    discover_files,
    process_single_file,
    CLONE_DIR
)
from .db import SessionLocal, Workspace

class IngestState(TypedDict):
    repo_url: str
    workspace_name: str
    generate_tags: bool
    max_files: Optional[int]
    background_tasks: Optional[Any]
    
    # Graph state
    repo_name: str
    workspace_id: int
    clone_path: str
    discovered_files: List[Dict]
    processed_count: int
    tagged_count: int
    indexed_count: int
    failed_count: int
    errors: List[str]
    status: str

def clone_node(state: IngestState) -> IngestState:
    """Clones the repository and ensures the workspace exists."""
    try:
        repo_name = state["repo_url"].rstrip('/').split('/')[-1].replace('.git', '')
        workspace_name = state["workspace_name"] or f"repo:{repo_name}"
        clone_path = clone_repo(state["repo_url"], CLONE_DIR)
        
        # Ensure workspace exists
        db = SessionLocal()
        workspace = db.query(Workspace).filter(Workspace.name == workspace_name).first()
        if not workspace:
            workspace = Workspace(name=workspace_name, description=f"GitHub repo: {state['repo_url']}")
            db.add(workspace)
            db.commit()
            db.refresh(workspace)
        workspace_id = workspace.id
        db.close()
        
        return {
            "repo_name": repo_name,
            "workspace_name": workspace_name,
            "workspace_id": workspace_id,
            "clone_path": clone_path,
            "status": "cloned"
        }
    except Exception as e:
        return {"status": "error", "errors": [f"Clone failed: {str(e)}"]}

def discover_node(state: IngestState) -> IngestState:
    """Discovers indexable files in the cloned repository."""
    try:
        files = discover_files(state["clone_path"])
        if state["max_files"]:
            files = files[:state["max_files"]]
            
        return {
            "discovered_files": files,
            "status": "discovered"
        }
    except Exception as e:
        return {"status": "error", "errors": state.get("errors", []) + [f"Discover failed: {str(e)}"]}

def process_node(state: IngestState) -> IngestState:
    """Processes, tags, and indexes the discovered files using a ThreadPoolExecutor."""
    indexed = 0
    tagged = 0
    failed = 0
    errors = state.get("errors", [])
    files = state["discovered_files"]
    
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(
                    process_single_file,
                    file_info, state["workspace_id"], state["workspace_name"], state["repo_name"], state["repo_url"], state["generate_tags"], state["background_tasks"]
                ): file_info for file_info in files
            }

            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                if res["status"] == "success":
                    indexed += 1
                    if res.get("tagged"):
                        tagged += 1
                elif res["status"] == "error":
                    failed += 1
                    if failed <= 5:
                        errors.append(f"Error on {res.get('path')}: {res.get('error')}")
                
        return {
            "processed_count": len(files),
            "tagged_count": tagged,
            "indexed_count": indexed,
            "failed_count": failed,
            "errors": errors,
            "status": "processed"
        }
    except Exception as e:
        return {"status": "error", "errors": errors + [f"Process failed: {str(e)}"]}

def check_errors(state: IngestState) -> str:
    """Routing edge based on errors."""
    if state.get("status") == "error":
        return END
    return "next"

# Build the LangGraph
def build_ingest_graph():
    workflow = StateGraph(IngestState)
    
    # Add nodes
    workflow.add_node("clone", clone_node)
    workflow.add_node("discover", discover_node)
    workflow.add_node("process", process_node)
    
    # Add edges
    workflow.add_edge(START, "clone")
    
    # Add conditional edge for error handling after clone
    workflow.add_conditional_edges(
        "clone",
        check_errors,
        {END: END, "next": "discover"}
    )
    
    # Add conditional edge after discover
    workflow.add_conditional_edges(
        "discover",
        check_errors,
        {END: END, "next": "process"}
    )
    
    workflow.add_edge("process", END)
    
    return workflow.compile()

# Instantiate the compiled graph
ingest_app = build_ingest_graph()

def run_ingest_graph(
    repo_url: str,
    workspace_name: Optional[str] = None,
    generate_tags: bool = True,
    max_files: Optional[int] = None,
    background_tasks: Optional[Any] = None,
) -> dict:
    """Entry point to run the ingestion workflow via LangGraph."""
    initial_state = {
        "repo_url": repo_url,
        "workspace_name": workspace_name,
        "generate_tags": generate_tags,
        "max_files": max_files,
        "background_tasks": background_tasks,
        "repo_name": "",
        "workspace_id": 0,
        "clone_path": "",
        "discovered_files": [],
        "processed_count": 0,
        "tagged_count": 0,
        "indexed_count": 0,
        "failed_count": 0,
        "errors": [],
        "status": "started"
    }
    
    # Execute the graph
    final_state = ingest_app.invoke(initial_state)
    
    return {
        "status": final_state.get("status"),
        "total": final_state.get("processed_count", 0),
        "indexed": final_state.get("indexed_count", 0),
        "tagged": final_state.get("tagged_count", 0),
        "failed": final_state.get("failed_count", 0),
        "errors": final_state.get("errors", [])
    }
