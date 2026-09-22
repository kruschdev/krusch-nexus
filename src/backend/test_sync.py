import os
import sys
import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Set Python path to ensure imports resolve
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import src.backend.test_stubs
from unittest.mock import MagicMock, patch
class MockFastAPI:
    def __init__(self, *args, **kwargs):
        pass
    def post(self, *args, **kwargs):
        return lambda f: f
    def get(self, *args, **kwargs):
        return lambda f: f
    def delete(self, *args, **kwargs):
        return lambda f: f
    def on_event(self, *args, **kwargs):
        return lambda f: f

from src.backend.db import Base, Employee, Workspace, Document as DocModel
from src.backend.sync_provider import LocalJsonDirectoryProvider
from src.backend.sync_service import sync_directory_data, sync_documents_data

class TestSyncPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Use an in-memory SQLite database for testing the logic cleanly
        cls.engine = create_engine("sqlite:///:memory:")
        cls.Session = sessionmaker(bind=cls.engine)
        Base.metadata.create_all(cls.engine)
        cls.db = cls.Session()

        # Create a mock workspace
        cls.workspace = Workspace(name="test_sync_ws", description="Testing Sync")
        cls.db.add(cls.workspace)
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_provider_loads_mock_data(self):
        provider = LocalJsonDirectoryProvider()
        users = provider.fetch_users()
        emails = provider.fetch_emails()
        files = provider.fetch_files()
        
        self.assertGreater(len(users), 0)
        self.assertGreater(len(emails), 0)
        self.assertGreater(len(files), 0)
        
        self.assertEqual(users[0]["email"], "alice.smith@example.com")
        self.assertEqual(emails[0]["sender"], "alice.smith@example.com")

    def test_directory_sync(self):
        # Run sync directory
        res = sync_directory_data("local", self.db)
        self.assertEqual(res["status"], "success")
        self.assertGreater(res["total_synced"], 0)
        
        # Verify db records exist
        employees = self.db.query(Employee).all()
        self.assertEqual(len(employees), res["total_synced"])
        
        alice = self.db.query(Employee).filter(Employee.email == "alice.smith@example.com").first()
        self.assertIsNotNone(alice)
        self.assertEqual(alice.first_name, "Alice")
        self.assertEqual(alice.job_title, "VP of Legal & Compliance")

        # Test upsert logic - change name in db and re-sync
        alice.first_name = "Alice Edited"
        self.db.commit()
        
        # Sync again
        res2 = sync_directory_data("local", self.db)
        self.assertEqual(res2["status"], "success")
        
        # Verify it reverted back to the provider value
        self.db.refresh(alice)
        self.assertEqual(alice.first_name, "Alice")

    @patch("src.backend.sync_service.index_documents")
    def test_document_sync(self, mock_index):
        mock_index.return_value = 5
        # Sync documents
        res = sync_documents_data("local", self.workspace.id, self.db)
        self.assertEqual(res["status"], "success")
        self.assertGreater(res["emails_synced"], 0)
        self.assertGreater(res["files_synced"], 0)
        
        # Verify db document records exist
        docs = self.db.query(DocModel).filter(DocModel.workspace_id == self.workspace.id).all()
        self.assertEqual(len(docs), res["emails_synced"] + res["files_synced"])
        
        # Verify idempotency (should sync 0 items on consecutive runs)
        res2 = sync_documents_data("local", self.workspace.id, self.db)
        self.assertEqual(res2["status"], "success")
        self.assertEqual(res2["emails_synced"], 0)
        self.assertEqual(res2["files_synced"], 0)

    def test_webhook_endpoint(self):
        from src.backend.main import email_webhook_ingest, EmailWebhookRequest
        
        request = EmailWebhookRequest(
            external_id="msg_webhook_999",
            sender="external.client@example.com",
            subject="Urgent request regarding compliance",
            body="This is a real-time webhook body text.",
            date="2026-07-10T22:15:00Z",
            workspace_id=self.workspace.id
        )
        
        resp = email_webhook_ingest(request, self.db)
        self.assertEqual(resp["status"], "success")
        
        # Verify db record was created
        doc = self.db.query(DocModel).filter(DocModel.filename == "email_msg_webhook_999.txt").first()
        self.assertIsNotNone(doc)
        self.assertEqual(doc.workspace_id, self.workspace.id)

    def test_slack_webhook_endpoint(self):
        from src.backend.main import slack_webhook_ingest, SlackWebhookPayload
        
        req_challenge = SlackWebhookPayload(
            type="url_verification",
            challenge="slack_verify_123"
        )
        res_challenge = slack_webhook_ingest(req_challenge, self.db)
        self.assertEqual(res_challenge["challenge"], "slack_verify_123")
        
        req_message = SlackWebhookPayload(
            type="event_callback",
            workspace_id=self.workspace.id,
            event={
                "type": "message",
                "user": "U12345",
                "channel": "C12345",
                "text": "Please double check the NDA term length.",
                "ts": "1620000000.000001"
            }
        )
        res_message = slack_webhook_ingest(req_message, self.db)
        self.assertEqual(res_message["status"], "success")
        
        doc = self.db.query(DocModel).filter(DocModel.filename == "slack_slack_C12345_1620000000.000001.txt").first()
        self.assertIsNotNone(doc)
        self.assertEqual(doc.workspace_id, self.workspace.id)

    def test_mobile_chat_route(self):
        from src.backend.main import get_mobile_chat
        resp = get_mobile_chat()
        self.assertIsNotNone(resp)

if __name__ == "__main__":
    unittest.main()
