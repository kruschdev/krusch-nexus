"""
Host Unit Testing Stubs for Krusch-Nexus
Pre-initializes sys.modules stubs for heavy AI libraries (llama_index, sentence_transformers, passlib)
so that test modules can run in host environment without Docker socket or GPU dependencies.
"""

import os
import sys
from unittest.mock import MagicMock

if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = os.getenv("DBOS_DATABASE_URL", "postgresql://openclaw:openclaw_password@10.0.0.85:5434/kruschdb")
    os.environ["DBOS_DATABASE_URL"] = os.environ["DATABASE_URL"]

class MockNode:
    def __init__(self, text="", metadata=None, node_id=None, relationships=None, **kwargs):
        self.text = text
        self.metadata = metadata if metadata is not None else {}
        self.node_id = node_id or "node_mock_123"
        self.id_ = self.node_id
        self.relationships = relationships if relationships is not None else {}
        self.__dict__.update(kwargs)
    def get_content(self):
        return self.text

class MockNodeWithScore:
    def __init__(self, node, score=1.0):
        if isinstance(node, str):
            node = MockNode(text=node)
        self.node = node
        self.score = score
    @property
    def node_id(self):
        return getattr(self.node, 'node_id', 'node_123')
    @property
    def metadata(self):
        return getattr(self.node, 'metadata', {})
    def get_content(self):
        if hasattr(self.node, 'get_content'):
            return self.node.get_content()
        return getattr(self.node, 'text', str(self.node))

class BaseNodePostprocessor:
    def __init__(self, **data):
        for k, v in data.items():
            setattr(self, k, v)

class MockDocument:
    def __init__(self, text="", metadata=None, **kwargs):
        self.text = text
        self.metadata = metadata if metadata is not None else {}
        self.__dict__.update(kwargs)
    def get_content(self):
        return self.text

class MockNodeRelationship:
    NEXT = "NEXT"
    PREVIOUS = "PREVIOUS"

def init_test_stubs():
    if 'llama_index' not in sys.modules or isinstance(sys.modules['llama_index'], MagicMock):
        llama_mock = MagicMock()
        llama_mock.__path__ = []
        
        core_mock = MagicMock()
        core_mock.__path__ = []
        schema_mock = MagicMock()
        schema_mock.NodeWithScore = MockNodeWithScore
        schema_mock.TextNode = MockNode
        schema_mock.NodeRelationship = MockNodeRelationship
        core_mock.Document = MockDocument
        core_mock.schema = schema_mock
        
        readers_mock = MagicMock()
        readers_mock.__path__ = []
        docling_mock = MagicMock()
        readers_mock.docling = docling_mock

        sys.modules['llama_index'] = llama_mock
        sys.modules['llama_index.core'] = core_mock
        sys.modules['llama_index.core.schema'] = schema_mock
        sys.modules['llama_index.core.vector_stores'] = MagicMock()
        sys.modules['llama_index.vector_stores'] = MagicMock()
        sys.modules['llama_index.vector_stores.postgres'] = MagicMock()
        sys.modules['llama_index.readers'] = readers_mock
        sys.modules['llama_index.readers.docling'] = docling_mock
        sys.modules['llama_index.llms'] = MagicMock()
        sys.modules['llama_index.llms.ollama'] = MagicMock()
        sys.modules['llama_index.embeddings'] = MagicMock()
        sys.modules['llama_index.embeddings.ollama'] = MagicMock()
        postproc_mock = MagicMock()
        postproc_mock.BaseNodePostprocessor = BaseNodePostprocessor
        sys.modules['llama_index.core.postprocessor'] = postproc_mock
        sys.modules['llama_index.core.postprocessor.types'] = postproc_mock

    if 'sentence_transformers' not in sys.modules:
        st_mock = MagicMock()
        class MockCrossEncoder:
            def __init__(self, *args, **kwargs):
                pass
            def predict(self, pairs):
                scores = []
                for pair in pairs:
                    if isinstance(pair, (list, tuple)) and len(pair) == 2:
                        query, text = pair
                        q_words = set(w.strip('?,.:;"\'').lower() for w in str(query).split())
                        t_words = set(w.strip('?,.:;"\'').lower() for w in str(text).split())
                        overlap = len(q_words.intersection(t_words))
                        scores.append(float(overlap) * 0.5 + 0.05)
                    else:
                        scores.append(0.5)
                return scores
        st_mock.CrossEncoder = MockCrossEncoder
        sys.modules['sentence_transformers'] = st_mock
    if 'passlib' not in sys.modules:
        sys.modules['passlib'] = MagicMock()
        sys.modules['passlib.context'] = MagicMock()
    if 'langgraph' not in sys.modules:
        langgraph_mock = MagicMock()
        graph_mock = MagicMock()
        
        def mock_invoke(state):
            return {
                "query": state.get("query", ""),
                "workspace_id": state.get("workspace_id", 1),
                "sources": [{"filename": "SOP_Refunds.pdf"}],
                "logic": "Step-by-step institutional audit analysis logic.",
                "claim": "Refund requests are processed per policy guidelines.",
                "_context": "Institutional context"
            }

        mock_compiled_app = MagicMock()
        mock_compiled_app.invoke.side_effect = mock_invoke
        mock_state_graph = MagicMock()
        mock_state_graph.return_value.compile.return_value = mock_compiled_app
        graph_mock.StateGraph = mock_state_graph
        graph_mock.END = "END"

        sys.modules['langgraph'] = langgraph_mock
        sys.modules['langgraph.graph'] = graph_mock

# Auto-initialize on import
init_test_stubs()
