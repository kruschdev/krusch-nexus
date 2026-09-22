import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Set Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import src.backend.test_stubs

from llama_index.core.schema import NodeWithScore, TextNode
from src.backend.rag_engine import RoleAclPostprocessor
from src.backend.db import Document

class TestClassificationFlagging(unittest.TestCase):

    def test_role_acl_postprocessor_management_only_filter(self):
        # Create test nodes
        public_node = NodeWithScore(
            node=TextNode(text="Public document", metadata={"classification_level": "public", "allowed_roles": "all"}),
            score=0.95
        )
        mgmt_node = NodeWithScore(
            node=TextNode(text="Management only financial report", metadata={"classification_level": "management_only", "allowed_roles": "admin,management"}),
            score=0.98
        )
        confidential_node = NodeWithScore(
            node=TextNode(text="Confidential M&A strategy", metadata={"classification_level": "confidential", "allowed_roles": "admin,management"}),
            score=0.99
        )

        nodes = [public_node, mgmt_node, confidential_node]

        # 1. Non-management user role ('user')
        user_processor = RoleAclPostprocessor(user_role="user")
        filtered_user = user_processor._postprocess_nodes(nodes)
        self.assertEqual(len(filtered_user), 1)
        self.assertEqual(filtered_user[0].node.text, "Public document")

        # 2. Management role ('management')
        mgmt_processor = RoleAclPostprocessor(user_role="management")
        filtered_mgmt = mgmt_processor._postprocess_nodes(nodes)
        self.assertEqual(len(filtered_mgmt), 3)

        # 3. Admin role ('admin')
        admin_processor = RoleAclPostprocessor(user_role="admin")
        filtered_admin = admin_processor._postprocess_nodes(nodes)
        self.assertEqual(len(filtered_admin), 3)

    def test_document_model_classification_fields(self):
        doc = Document(
            filename="q3_salaries.pdf",
            workspace_id=1,
            classification_level="management_only",
            allowed_roles="admin,management",
            flagged_for_review=True,
            flag_reason="Contains unredacted executive salary data"
        )
        self.assertEqual(doc.classification_level, "management_only")
        self.assertEqual(doc.allowed_roles, "admin,management")
        self.assertTrue(doc.flagged_for_review)
        self.assertEqual(doc.flag_reason, "Contains unredacted executive salary data")

if __name__ == "__main__":
    unittest.main()
