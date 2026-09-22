import os
import sys
import unittest
from unittest.mock import MagicMock, patch
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Set Python path to ensure imports resolve
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import src.backend.test_stubs
import src.backend.rag_engine as rag_engine
import src.backend.data_flywheel as data_flywheel
from src.backend.db import Base, Workspace, Document, GraphNode, GraphEdge, User
from src.backend.main import get_coordination_conflicts, resolve_coordination_conflict

class TestCompanyBrain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create in-memory SQLite DB
        cls.engine = create_engine("sqlite:///:memory:")
        cls.Session = sessionmaker(bind=cls.engine)
        Base.metadata.create_all(cls.engine)
        cls.db = cls.Session()

        # Add a test workspace
        cls.workspace = Workspace(id=1, name="test_ws", description="Test Workspace")
        cls.db.add(cls.workspace)
        cls.db.commit()

        # Add mock documents
        cls.doc_sales = Document(id=1, filename="sales_NDA.txt", workspace_id=1)
        cls.doc_product = Document(id=2, filename="product_roadmap.txt", workspace_id=1)
        cls.db.add(cls.doc_sales)
        cls.db.add(cls.doc_product)
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    @patch('src.backend.rag_engine.SessionLocal')
    @patch('src.backend.rag_engine.llm_fast')
    def test_conflict_auditing_and_serialization(self, mock_fast, mock_session):
        """Verify that a coordination conflict is detected, serialized, and resolved."""
        mock_session.return_value = self.db
        
        # 1. Mock LLM returning a valid conflict JSON
        mock_fast.complete.return_value = json.dumps({
            "conflict_found": True,
            "title": "SOC2 vs NDA Retention Conflict",
            "claim": "Sales NDA says 3 years data retention but Product Policy says 5 years.",
            "logic": "1. NDA.txt specifies 3 years.\n2. Roadmap.txt specifies 5 years."
        })
        
        # 2. Run audit
        conflict_id = rag_engine.audit_document_pair(doc_a_id=1, doc_b_id=2, workspace_id=1)
        self.assertIsNotNone(conflict_id)
        
        # 3. Check graph node was created
        conflict_node = self.db.query(GraphNode).filter(GraphNode.id == conflict_id).first()
        self.assertIsNotNone(conflict_node)
        self.assertEqual(conflict_node.entity_name, "SOC2 vs NDA Retention Conflict")
        self.assertEqual(conflict_node.entity_type, "Conflict")
        self.assertEqual(conflict_node.status, "active")
        
        meta = json.loads(conflict_node.custom_metadata)
        self.assertEqual(meta["claim"], "Sales NDA says 3 years data retention but Product Policy says 5 years.")
        
        # 4. Check edges were created
        edges = self.db.query(GraphEdge).filter(GraphEdge.source_node_id == conflict_id).all()
        self.assertEqual(len(edges), 2)
        self.assertEqual(edges[0].relationship_type, "contradicts")
        self.assertEqual(edges[0].status, "active")

        # 5. Verify the API GET route returns active conflicts
        mock_user = User(username="admin", role="admin")
        
        results = get_coordination_conflicts(workspace_id=1, db=self.db, current_user=mock_user)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "SOC2 vs NDA Retention Conflict")
        self.assertEqual(len(results[0]["sources"]), 2)
        
        # 6. Verify the API POST route resolves conflicts
        resolve_res = resolve_coordination_conflict(conflict_id=conflict_id, db=self.db, current_user=mock_user)
        self.assertEqual(resolve_res["status"], "success")
        
        # Verify db updates
        self.db.refresh(conflict_node)
        self.assertEqual(conflict_node.status, "resolved")
        for edge in edges:
            self.db.refresh(edge)
            self.assertEqual(edge.status, "resolved")
            
        # Get conflicts again, should be empty (since status is resolved)
        results_resolved = get_coordination_conflicts(workspace_id=1, db=self.db, current_user=mock_user)
        self.assertEqual(len(results_resolved), 0)

    @patch('src.backend.data_flywheel.SessionLocal')
    @patch('src.backend.data_flywheel.llm_fast')
    def test_nightly_memory_consolidation(self, mock_fast, mock_session):
        """Verify that multiple active conflicts are consolidated into an Invariant and archived."""
        original_close = self.db.close
        self.db.close = MagicMock()
        try:
            mock_session.return_value = self.db
            
            conflict1 = GraphNode(
                entity_name="Conflict Alpha",
                entity_type="Conflict",
                workspace_id=1,
                status="active",
                custom_metadata=json.dumps({"claim": "Contradiction A"})
            )
            conflict2 = GraphNode(
                entity_name="Conflict Beta",
                entity_type="Conflict",
                workspace_id=1,
                status="active",
                custom_metadata=json.dumps({"claim": "Contradiction B"})
            )
            self.db.add(conflict1)
            self.db.add(conflict2)
            self.db.commit()
            
            mock_fast.complete.return_value = json.dumps({
                "consolidate": True,
                "title": "Consolidated Retention Invariant",
                "invariant_rule": "Always defer to the longer retention period (5 years) for all legal and compliance reviews.",
                "consolidated_node_names": ["Conflict Alpha", "Conflict Beta"]
            })
            
            from src.backend.data_flywheel import nightly_memory_consolidation_loop
            
            with patch('asyncio.sleep', side_effect=Exception("StopLoop")):
                try:
                    import asyncio
                    asyncio.run(nightly_memory_consolidation_loop())
                except Exception as e:
                    self.assertEqual(str(e), "StopLoop")
                    
            invariant_node = self.db.query(GraphNode).filter(
                GraphNode.entity_name == "Consolidated Retention Invariant",
                GraphNode.entity_type == "Invariant",
                GraphNode.workspace_id == 1
            ).first()
            self.assertIsNotNone(invariant_node)
            self.assertEqual(invariant_node.status, "active")
            
            meta = json.loads(invariant_node.custom_metadata)
            self.assertEqual(meta["invariant_rule"], "Always defer to the longer retention period (5 years) for all legal and compliance reviews.")
            
            self.db.refresh(conflict1)
            self.db.refresh(conflict2)
            self.assertEqual(conflict1.status, "archived")
            self.assertEqual(conflict2.status, "archived")
            
            edge1 = self.db.query(GraphEdge).filter(
                GraphEdge.source_node_id == invariant_node.id,
                GraphEdge.target_node_id == conflict1.id,
                GraphEdge.relationship_type == "resolves"
            ).first()
            self.assertIsNotNone(edge1)
            self.assertEqual(edge1.status, "active")
        finally:
            self.db.close = original_close

if __name__ == "__main__":
    unittest.main()
