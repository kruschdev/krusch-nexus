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

# Stub out LlamaIndex schema classes
class MockNode:
    def __init__(self, text, metadata=None, id_="node_123", node_id=None, relationships=None):
        self.text = text
        self.metadata = metadata or {}
        self.node_id = node_id or id_
        self.relationships = relationships or {}
    def get_content(self):
        return self.text

class MockNodeWithScore:
    def __init__(self, node, score=1.0):
        self.node = node
        self.score = score
    def get_content(self):
        return self.node.text
    @property
    def metadata(self):
        return self.node.metadata
    @property
    def node_id(self):
        return self.node.node_id

sys.modules['llama_index.core.schema'].TextNode = MockNode
sys.modules['llama_index.core.schema'].NodeWithScore = MockNodeWithScore

# Mock relationships enum/class
class MockNodeRelationship:
    PREVIOUS = "PREVIOUS"
    NEXT = "NEXT"
sys.modules['llama_index.core.schema'].NodeRelationship = MockNodeRelationship

# Now import the modules under test
import src.backend.rag_engine as rag_engine
from src.backend.db import Base, Workspace, Document

class TestGrasp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create in-memory SQLite DB
        cls.engine = create_engine("sqlite:///:memory:")
        cls.Session = sessionmaker(bind=cls.engine)
        Base.metadata.create_all(cls.engine)
        cls.db = cls.Session()

        # Add a test workspace and document
        cls.workspace = Workspace(id=1, name="test_ws", description="Test Workspace")
        cls.doc = Document(id=10, filename="contract.pdf", workspace_id=1)
        cls.db.add(cls.workspace)
        cls.db.add(cls.doc)
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    @patch('src.backend.rag_engine.SessionLocal')
    @patch('src.backend.rag_engine.get_llm_for_query')
    @patch('src.backend.rag_engine.get_vector_store')
    @patch('src.backend.rag_engine.VectorStoreIndex')
    @patch('src.backend.rag_engine.reranker_instance')
    def test_grasp_stop_immediately(self, mock_reranker, mock_index_class, mock_vector_store, mock_get_llm, mock_session):
        """Verify that GRASP retrieval loop calls LLM and stops immediately if requested."""
        mock_session.return_value = self.db
        
        # Mock semantic retriever returning 1 node
        mock_node = MockNode("Initial semantic match text.", metadata={"workspace_id": 1, "document_id": 10})
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = [MockNodeWithScore(mock_node)]
        
        mock_index = MagicMock()
        mock_index.as_retriever.return_value = mock_retriever
        mock_index_class.from_vector_store.return_value = mock_index
        
        # Mock LLM returning STOP action
        mock_llm = MagicMock()
        mock_llm.complete.return_value = json.dumps({"action": "STOP"})
        mock_get_llm.return_value = mock_llm
        
        # Mock reranker to return unchanged nodes
        mock_reranker.postprocess_nodes.side_effect = lambda nodes, **kwargs: nodes
        
        state = {
            "query": "Is there an NDA?",
            "workspace_id": 1,
            "sources": [],
            "logic": "",
            "claim": "",
            "_context": ""
        }
        
        result_state = rag_engine.retrieve_node(state)
        
        # Assertions
        self.assertEqual(len(result_state["sources"]), 1)
        self.assertIn("Initial semantic match", result_state["_context"])
        mock_llm.complete.assert_called_once()

    @patch('src.backend.rag_engine.SessionLocal')
    @patch('src.backend.rag_engine.get_llm_for_query')
    @patch('src.backend.rag_engine.get_vector_store')
    @patch('src.backend.rag_engine.VectorStoreIndex')
    @patch('src.backend.rag_engine.reranker_instance')
    @patch('src.backend.rag_engine.db_keyword_search')
    @patch('src.backend.rag_engine.db_fetch_node_by_id')
    def test_grasp_keyword_and_expand_context(self, mock_fetch_node, mock_keyword_search, mock_reranker, mock_index_class, mock_vector_store, mock_get_llm, mock_session):
        """Verify that GRASP retrieval performs keyword searches and context expansion."""
        mock_session.return_value = self.db
        
        # Mock semantic retriever returning 1 node with relationships
        mock_rel = MagicMock()
        mock_rel.node_id = "node_sibling_456"
        mock_node = MockNode("Base chunk text.", metadata={"workspace_id": 1}, node_id="node_123", relationships={"NEXT": mock_rel})
        
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = [MockNodeWithScore(mock_node)]
        
        mock_index = MagicMock()
        mock_index.as_retriever.return_value = mock_retriever
        mock_index_class.from_vector_store.return_value = mock_index
        
        # Mock LLM calling sequence:
        # Turn 1: KEYWORD_SEARCH
        # Turn 2: EXPAND_CONTEXT
        # Turn 3: STOP
        mock_llm = MagicMock()
        mock_llm.complete.side_effect = [
            json.dumps({"action": "KEYWORD_SEARCH", "query": "liability"}),
            json.dumps({"action": "EXPAND_CONTEXT", "node_index": 0, "direction": "NEXT"}),
            json.dumps({"action": "STOP"})
        ]
        mock_get_llm.return_value = mock_llm
        
        # Mock keyword search response
        mock_keyword_search.return_value = [
            {"text": "Keyword match text.", "metadata": {"workspace_id": 1}, "id": "node_kw_789"}
        ]
        
        # Mock sibling fetch response
        mock_fetch_node.return_value = {
            "text": "Expanded sibling text.", "metadata": {"workspace_id": 1}, "id": "node_sibling_456"
        }
        
        # Mock reranker to return unchanged nodes
        mock_reranker.postprocess_nodes.side_effect = lambda nodes, *args, **kwargs: nodes
        
        state = {
            "query": "What is the liability limit?",
            "workspace_id": 1,
            "sources": [],
            "logic": "",
            "claim": "",
            "_context": ""
        }
        
        result_state = rag_engine.retrieve_node(state)
        
        # Assertions
        # Expecting 3 nodes: Initial, Keyword match, and Sibling match
        self.assertEqual(len(result_state["sources"]), 3)
        self.assertIn("Base chunk text", result_state["_context"])
        self.assertIn("Keyword match text", result_state["_context"])
        self.assertIn("Expanded sibling text", result_state["_context"])

    @patch('src.backend.rag_engine.SessionLocal')
    @patch('src.backend.rag_engine.get_llm_for_query')
    @patch('src.backend.rag_engine.retrieve_node_static')
    def test_grasp_fallback_on_error(self, mock_static, mock_get_llm, mock_session):
        """Verify that GRASP falls back to retrieve_node_static if the loop throws an exception."""
        mock_session.return_value = self.db
        
        # Mock LLM to throw an exception
        mock_llm = MagicMock()
        mock_llm.complete.side_effect = Exception("Ollama is down or rate-limited")
        mock_get_llm.return_value = mock_llm
        
        # Mock static retrieve to return a success indicator
        mock_static.return_value = {"_context": "fallback context content", "sources": [{"workspace_id": 1}]}
        
        state = {
            "query": "Is there a conflict?",
            "workspace_id": 1,
            "sources": [],
            "logic": "",
            "claim": "",
            "_context": ""
        }
        
        result_state = rag_engine.retrieve_node(state)
        
        # Assertions
        self.assertIn("fallback context", result_state["_context"])
        mock_static.assert_called_once_with(state)

if __name__ == "__main__":
    unittest.main()
