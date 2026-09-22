import os
import sys
import unittest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure python path is set correctly
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import src.backend.test_stubs

class MockBaseNodePostprocessor:
    def __init__(self, *args, **kwargs):
        pass

sys.modules['llama_index.core.postprocessor.types'].BaseNodePostprocessor = MockBaseNodePostprocessor

class MockFastAPI:
    def __init__(self, *args, **kwargs):
        pass
    def post(self, *args, **kwargs):
        return lambda f: f
    def get(self, *args, **kwargs):
        return lambda f: f
    def on_event(self, *args, **kwargs):
        return lambda f: f

from src.backend.db import Base, Employee, Workspace, Document as DocModel, User
from src.backend.sync_provider import (
    JiraSyncProvider,
    ConfluenceSyncProvider,
    NotionSyncProvider,
    Microsoft365SyncProvider
)
from src.backend.sync_service import sync_directory_data, sync_documents_data
import src.backend.rag_engine as rag_engine
from src.backend.rag_engine import RoleAclPostprocessor


class TestNexusIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:")
        cls.Session = sessionmaker(bind=cls.engine)
        Base.metadata.create_all(cls.engine)
        cls.db = cls.Session()

        # Seed workspace
        cls.workspace = Workspace(name="test_integration_ws", description="Integration Tests")
        cls.db.add(cls.workspace)
        cls.db.commit()

        # Seed employees for Expert Finder test
        cls.emp1 = Employee(
            provider="jira",
            external_id="jira_301",
            email="david.vance@example.com",
            first_name="David",
            last_name="Vance",
            display_name="David Vance",
            job_title="Lead Software Engineer",
            department="Engineering",
            status="active"
        )
        cls.emp2 = Employee(
            provider="m365",
            external_id="m365_601",
            email="carol.danvers@example.com",
            first_name="Carol",
            last_name="Danvers",
            display_name="Carol Danvers",
            job_title="Director of Product",
            department="Product Management",
            status="active"
        )
        cls.db.add_all([cls.emp1, cls.emp2])
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_saas_sync_providers(self):
        """Verify that Jira, Confluence, Notion, and M365 providers fetch structured data."""
        jira = JiraSyncProvider()
        self.assertGreater(len(jira.fetch_users()), 0)
        self.assertGreater(len(jira.fetch_files()), 0)

        confluence = ConfluenceSyncProvider()
        self.assertGreater(len(confluence.fetch_files()), 0)

        notion = NotionSyncProvider()
        self.assertGreater(len(notion.fetch_files()), 0)

        m365 = Microsoft365SyncProvider()
        self.assertGreater(len(m365.fetch_users()), 0)
        self.assertGreater(len(m365.fetch_emails()), 0)
        self.assertGreater(len(m365.fetch_files()), 0)

    @patch("src.backend.sync_service.index_documents")
    def test_jira_and_m365_sync_services(self, mock_index):
        """Verify syncing Jira and M365 connectors through sync_service."""
        mock_index.return_value = None
        res_jira = sync_documents_data("jira", self.workspace.id, self.db)
        self.assertEqual(res_jira["status"], "success")

        res_m365 = sync_documents_data("m365", self.workspace.id, self.db)
        self.assertEqual(res_m365["status"], "success")

    def test_expert_finder_sme_router(self):
        """Verify Subject-Matter Expert (SME) router ranks employees based on role and query match."""
        # Query matching Engineering Lead
        res_eng = rag_engine.query_expert_finder("software engineer microservices auth", [self.workspace.id], db=self.db)
        self.assertIn("experts", res_eng)
        self.assertGreater(len(res_eng["experts"]), 0)
        top_expert = res_eng["experts"][0]
        self.assertEqual(top_expert["display_name"], "David Vance")
        self.assertEqual(top_expert["job_title"], "Lead Software Engineer")

        # Query matching Product Director
        res_prod = rag_engine.query_expert_finder("product management roadmap goals", [self.workspace.id], db=self.db)
        top_prod = res_prod["experts"][0]
        self.assertEqual(top_prod["display_name"], "Carol Danvers")
        self.assertEqual(top_prod["job_title"], "Director of Product")

    def test_role_acl_postprocessor(self):
        """Verify document-level ACL postprocessor filters results by user role."""
        postprocessor = RoleAclPostprocessor()
        postprocessor.user_role = "user"

        from src.backend.test_stubs import MockNode, MockNodeWithScore
        # Create mock NodeWithScore items
        node_public = MockNodeWithScore(MockNode(text="public text", metadata={"allowed_roles": "all", "filename": "public.txt"}))
        node_admin_only = MockNodeWithScore(MockNode(text="admin text", metadata={"allowed_roles": "admin", "filename": "admin_secrets.txt"}))

        nodes = [node_public, node_admin_only]

        # Non-admin user should only get public node
        filtered_user = postprocessor._postprocess_nodes(nodes)
        self.assertEqual(len(filtered_user), 1)
        self.assertEqual(filtered_user[0].node.metadata["filename"], "public.txt")

        # Admin user should get both nodes
        postprocessor_admin = RoleAclPostprocessor()
        postprocessor_admin.user_role = "admin"
        filtered_admin = postprocessor_admin._postprocess_nodes(nodes)
        self.assertEqual(len(filtered_admin), 2)


if __name__ == "__main__":
    unittest.main()
