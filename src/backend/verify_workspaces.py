import sys
import os
import requests

# Add current path to sys.path so we can import src.backend
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import SessionLocal, Workspace, GraphNode, GraphEdge

def run_test():
    db = SessionLocal()
    
    # 1. Create Workspaces
    wA = db.query(Workspace).filter(Workspace.name == "Workspace A").first()
    if not wA:
        wA = Workspace(name="Workspace A", description="Test")
        db.add(wA)
        
    wB = db.query(Workspace).filter(Workspace.name == "Workspace B").first()
    if not wB:
        wB = Workspace(name="Workspace B", description="Test")
        db.add(wB)
        
    db.commit()
    
    from db import Document
    docA = Document(filename="docA.txt", workspace_id=wA.id)
    docB = Document(filename="docB.txt", workspace_id=wB.id)
    db.add_all([docA, docB])
    db.commit()
    
    # 2. Add Graph Nodes & Edges to Workspace A
    nA1 = GraphNode(entity_name="Project Alpha", entity_type="Project", workspace_id=wA.id, document_id=docA.id)
    nA2 = GraphNode(entity_name="Alice", entity_type="Person", workspace_id=wA.id, document_id=docA.id)
    db.add_all([nA1, nA2])
    db.commit()
    
    eA = GraphEdge(source_node_id=nA2.id, target_node_id=nA1.id, relationship_type="manages", workspace_id=wA.id, document_id=docA.id)
    db.add(eA)
    
    # 3. Add Graph Nodes & Edges to Workspace B
    nB1 = GraphNode(entity_name="Project Beta", entity_type="Project", workspace_id=wB.id, document_id=docB.id)
    nB2 = GraphNode(entity_name="Bob", entity_type="Person", workspace_id=wB.id, document_id=docB.id)
    db.add_all([nB1, nB2])
    db.commit()
    
    eB = GraphEdge(source_node_id=nB2.id, target_node_id=nB1.id, relationship_type="leads", workspace_id=wB.id, document_id=docB.id)
    db.add(eB)
    db.commit()
    wA_id = wA.id
    wB_id = wB.id
    db.close()
    
    print("Database seeded with isolated graph nodes for Workspace A and Workspace B.")

    # Now we test via the API using curl / requests
    # Login as admin
    r = requests.post("http://localhost:8000/api/token", data={"username": "admin", "password": "admin"})
    token = r.json().get("access_token")
    headers = {"Authorization": f"Bearer {token}"}

    print("\n[Test 1] Querying Cross-Workspace API for Workspace A only:")
    payload_A = {"query": "Who manages Project Alpha?", "workspace_ids": [wA_id]}
    res_A = requests.post("http://localhost:8000/api/query-cross-workspace", json=payload_A, headers=headers)
    print(res_A.json())

    print("\n[Test 2] Querying Cross-Workspace API for Workspace B only:")
    payload_B = {"query": "Who leads Project Beta?", "workspace_ids": [wB_id]}
    res_B = requests.post("http://localhost:8000/api/query-cross-workspace", json=payload_B, headers=headers)
    print(res_B.json())

    print("\n[Test 3] Querying Cross-Workspace API for BOTH Workspaces:")
    payload_Both = {"query": "List all projects and their leaders.", "workspace_ids": [wA_id, wB_id]}
    res_Both = requests.post("http://localhost:8000/api/query-cross-workspace", json=payload_Both, headers=headers)
    print(res_Both.json())

if __name__ == "__main__":
    run_test()
