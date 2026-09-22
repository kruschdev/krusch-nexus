import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Set Python path to ensure imports resolve
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import src.backend.test_stubs
import src.backend.rag_engine as rag_engine

class TestAlignment(unittest.TestCase):
    @patch('src.backend.rag_engine.get_relevant_alignment_signals')
    @patch('src.backend.rag_engine.get_vector_store')
    @patch('src.backend.rag_engine.VectorStoreIndex')
    @patch('src.backend.rag_engine.llm_reasoning')
    def test_proactive_nudge_analysis_injects_alignment_signals(self, mock_llm_reasoning, mock_vector_store_index, mock_get_vector_store, mock_get_signals):
        # Setup mocks
        mock_get_signals.return_value = [
            {
                "query": "Let's benchmark on /dev/sdc",
                "nudge": "Warning: OS drive is protected.",
                "diff": "- Cwd: /dev/sdc\n+ Cwd: /mnt/nvme",
                "similarity": 0.95
            }
        ]
        
        # Mock index/retriever
        mock_retriever = MagicMock()
        mock_node_with_score = MagicMock()
        mock_node_with_score.node.get_content.return_value = "Retrieved document context snippet"
        mock_retriever.retrieve.return_value = [mock_node_with_score]
        
        mock_index_instance = MagicMock()
        mock_index_instance.as_retriever.return_value = mock_retriever
        mock_vector_store_index.from_vector_store.return_value = mock_index_instance
        
        # Call under test
        query = "Benchmark OS drive"
        workspace_id = 1
        
        rag_engine.get_proactive_nudge_analysis(query, workspace_id)
        
        # Verify get_relevant_alignment_signals was called with query
        mock_get_signals.assert_called_once_with(query, limit=2)
        
        # Verify that prompt generated and passed to llm_reasoning contains the mock signals diff
        args, kwargs = mock_llm_reasoning.complete.call_args
        prompt_passed = args[0]
        self.assertIn("### Reusable Alignment Guidance (Past Corrections):", prompt_passed)
        self.assertIn("Warning: OS drive is protected.", prompt_passed)
        self.assertIn("- Cwd: /dev/sdc", prompt_passed)
        
    @patch('src.backend.rag_engine.get_relevant_alignment_signals')
    @patch('src.backend.rag_engine.get_vector_store')
    @patch('src.backend.rag_engine.VectorStoreIndex')
    @patch('src.backend.rag_engine.llm_reasoning')
    def test_proactive_nudge_no_workspace_matches_but_has_alignment_signals(self, mock_llm_reasoning, mock_vector_store_index, mock_get_vector_store, mock_get_signals):
        # When no workspace doc is matched, but alignment signals exist, it should still audit
        mock_get_signals.return_value = [
            {
                "query": "Let's benchmark on /dev/sdc",
                "nudge": "Warning: OS drive is protected.",
                "diff": "- Cwd: /dev/sdc\n+ Cwd: /mnt/nvme",
                "similarity": 0.95
            }
        ]
        
        # Mock index/retriever returning empty list
        mock_retriever = MagicMock()
        mock_retriever.retrieve.return_value = []
        
        mock_index_instance = MagicMock()
        mock_index_instance.as_retriever.return_value = mock_retriever
        mock_vector_store_index.from_vector_store.return_value = mock_index_instance
        
        # Call under test
        query = "Benchmark OS drive"
        workspace_id = 1
        
        rag_engine.get_proactive_nudge_analysis(query, workspace_id)
        
        # Verify complete was called
        mock_llm_reasoning.complete.assert_called_once()
        args, _ = mock_llm_reasoning.complete.call_args
        prompt_passed = args[0]
        self.assertIn("No workspace document matches found.", prompt_passed)
        self.assertIn("Warning: OS drive is protected.", prompt_passed)

if __name__ == '__main__':
    unittest.main()
