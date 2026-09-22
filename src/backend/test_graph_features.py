import os
import sys

# Ensure backend path is in sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.backend.test_stubs

from src.backend.db import SessionLocal, Workspace, Document, GraphNode, GraphEdge
from src.backend.rag_engine import serialize_audit_trail_to_graph, query_with_audit_trail, LocalCrossEncoderReranker
from llama_index.core.schema import NodeWithScore, TextNode
from llama_index.core import QueryBundle

def test_context_observability_coupling():
    print("--- Testing Context-Observability Coupling ---")
    db = SessionLocal()
    try:
        # 1. Create or get test workspace
        ws = db.query(Workspace).filter(Workspace.name == "Test Graph Workspace").first()
        if not ws:
            ws = Workspace(name="Test Graph Workspace", description="Temporary workspace for testing graph memory features")
            db.add(ws)
            db.commit()
            db.refresh(ws)
        workspace_id = ws.id
        print(f"Using workspace ID: {workspace_id}")

        # 2. Setup mock source document in the DB
        doc = db.query(Document).filter(Document.filename == "test_source_doc.txt", Document.workspace_id == workspace_id).first()
        if not doc:
            doc = Document(filename="test_source_doc.txt", workspace_id=workspace_id)
            db.add(doc)
            db.commit()
            db.refresh(doc)

        # 3. Call serialization function
        query_str = "Who is the lead architect of Project Hydra?"
        claim = "The lead architect is Dr. Elizabeth Shaw."
        logic = "Based on test_source_doc.txt, Dr. Elizabeth Shaw is cited as the primary architect."
        sources = [{"document_id": doc.id, "filename": "test_source_doc.txt"}]

        serialize_audit_trail_to_graph(query_str, claim, logic, sources, workspace_id)

        # 4. Verify DB entries
        q_node = db.query(GraphNode).filter(GraphNode.entity_name == query_str[:255], GraphNode.entity_type == "Query", GraphNode.workspace_id == workspace_id).first()
        l_node = db.query(GraphNode).filter(GraphNode.entity_name == logic[:255], GraphNode.entity_type == "Logic", GraphNode.workspace_id == workspace_id).first()
        c_node = db.query(GraphNode).filter(GraphNode.entity_name == claim[:255], GraphNode.entity_type == "Claim", GraphNode.workspace_id == workspace_id).first()
        doc_node = db.query(GraphNode).filter(GraphNode.entity_name == "test_source_doc.txt", GraphNode.entity_type == "Document", GraphNode.workspace_id == workspace_id).first()

        assert q_node is not None, "Query node not created"
        assert l_node is not None, "Logic node not created"
        assert c_node is not None, "Claim node not created"
        assert doc_node is not None, "Document node not created"

        edge_q_l = db.query(GraphEdge).filter(GraphEdge.source_node_id == q_node.id, GraphEdge.target_node_id == l_node.id, GraphEdge.relationship_type == "has_logic").first()
        edge_l_c = db.query(GraphEdge).filter(GraphEdge.source_node_id == l_node.id, GraphEdge.target_node_id == c_node.id, GraphEdge.relationship_type == "yields_claim").first()
        edge_l_d = db.query(GraphEdge).filter(GraphEdge.source_node_id == l_node.id, GraphEdge.target_node_id == doc_node.id, GraphEdge.relationship_type == "cites_source").first()

        assert edge_q_l is not None, "Query -> Logic edge not created"
        assert edge_l_c is not None, "Logic -> Claim edge not created"
        assert edge_l_d is not None, "Logic -> Document edge not created"

        print("✅ Context-Observability Coupling verified successfully!")
        return workspace_id, doc.id
    finally:
        db.close()

def test_graph_relation_boosting(workspace_id, document_id):
    print("\n--- Testing Graph-Relation Boosting ---")
    db = SessionLocal()
    try:
        # Seed Graph Relations:
        # Query Entity: "AlphaCorp"
        # Connected Entity: "BetaSys"
        # Edge: AlphaCorp -> partner_with -> BetaSys
        n1 = db.query(GraphNode).filter(GraphNode.entity_name == "AlphaCorp", GraphNode.workspace_id == workspace_id).first()
        if not n1:
            n1 = GraphNode(entity_name="AlphaCorp", entity_type="Company", workspace_id=workspace_id, document_id=document_id)
            db.add(n1)
            db.flush()

        n2 = db.query(GraphNode).filter(GraphNode.entity_name == "BetaSys", GraphNode.workspace_id == workspace_id).first()
        if not n2:
            n2 = GraphNode(entity_name="BetaSys", entity_type="Company", workspace_id=workspace_id, document_id=document_id)
            db.add(n2)
            db.flush()

        edge = db.query(GraphEdge).filter(GraphEdge.source_node_id == n1.id, GraphEdge.target_node_id == n2.id).first()
        if not edge:
            edge = GraphEdge(source_node_id=n1.id, target_node_id=n2.id, relationship_type="partner_with", workspace_id=workspace_id, document_id=document_id)
            db.add(edge)
            db.commit()

        # Instantiate Reranker
        reranker = LocalCrossEncoderReranker(top_n=3)

        # Create two test nodes (document chunks):
        # Node A: Mentions "BetaSys" (should be boosted because it is connected to "AlphaCorp" in the query)
        # Node B: Mentions "GammaCorp" (neutral)
        node_a = NodeWithScore(
            node=TextNode(text="BetaSys is providing the database components.", metadata={"workspace_id": workspace_id}),
            score=0.2
        )
        node_b = NodeWithScore(
            node=TextNode(text="GammaCorp is completely unrelated to anything else.", metadata={"workspace_id": workspace_id}),
            score=0.3
        )

        nodes = [node_a, node_b]
        query_bundle = QueryBundle(query_str="What is happening with AlphaCorp?")

        # Post-process nodes
        processed_nodes = reranker.postprocess_nodes(nodes, query_bundle=query_bundle)

        print("\nReranking Results:")
        for pn in processed_nodes:
            boosted = pn.node.metadata.get("graph_boosted", False)
            print(f"- Node: '{pn.node.get_content()}', Score: {pn.score:.4f}, Boosted: {boosted}")

        # Check that Node A (which contains "BetaSys") got the "+0.15" boost
        # Since Node B has score 0.3 and Node A had 0.2 + 0.15 = 0.35, Node A should now rank #1
        assert processed_nodes[0].node.get_content().startswith("BetaSys"), "Node A was not boosted to top rank"
        assert processed_nodes[0].node.metadata.get("graph_boosted") is True, "Boost metadata flag not set"

        print("✅ Graph-Relation Boosting verified successfully!")
    finally:
        db.close()

if __name__ == "__main__":
    try:
        workspace_id, doc_id = test_context_observability_coupling()
        test_graph_relation_boosting(workspace_id, doc_id)
        print("\n🎉 ALL TESTS PASSED SUCCESSFULLY!")
    except Exception as e:
        print(f"\n❌ Test Failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
