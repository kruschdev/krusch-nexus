import os
import sys
import json
import unittest
from unittest.mock import MagicMock, patch

# Set PYTHONPATH
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import src.backend.test_stubs

from src.backend.mcp_server import (
    nexus_list_workspaces,
    nexus_list_documents,
    nexus_query_business_knowledge,
    nexus_get_email_draft_context,
    nexus_ingest_business_document,
    nexus_get_sop_checklist,
    nexus_find_expert
)

class TestBusinessRAGMCPTools(unittest.TestCase):

    @patch("src.backend.db.SessionLocal")
    def test_nexus_list_workspaces(self, mock_session_cls):
        mock_db = MagicMock()
        mock_ws = MagicMock()
        mock_ws.id = 1
        mock_ws.name = "General"
        mock_ws.description = "Test workspace"
        mock_db.query().all.return_value = [mock_ws]
        mock_session_cls.return_value = mock_db

        res_json = nexus_list_workspaces()
        data = json.loads(res_json)
        self.assertIn("workspaces", data)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["workspaces"][0]["name"], "General")

    @patch("src.backend.db.SessionLocal")
    def test_nexus_list_documents(self, mock_session_cls):
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.id = 10
        mock_doc.filename = "SOP_Refunds.pdf"
        mock_doc.workspace_id = 1
        mock_doc.created_at = "2026-07-28"
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = [mock_doc]
        mock_db.query.return_value = mock_query
        mock_session_cls.return_value = mock_db

        res_json = nexus_list_documents(workspace_name_or_id="1")
        data = json.loads(res_json)
        self.assertIn("documents", data)
        self.assertEqual(data["documents"][0]["filename"], "SOP_Refunds.pdf")

    @patch("src.backend.rag_engine.query_cross_workspace_graph")
    @patch("src.backend.db.SessionLocal")
    def test_nexus_query_business_knowledge(self, mock_session_cls, mock_cross_query):
        mock_cross_query.return_value = {
            "response": "Refunds are processed within 3 business days per SOP-102.",
            "sources": [{"filename": "SOP_Refunds.pdf"}]
        }

        res_json = nexus_query_business_knowledge("What is the refund policy?")
        data = json.loads(res_json)
        self.assertEqual(data["status"], "success")
        self.assertIn("3 business days", data["response"])

    @patch("src.backend.rag_engine.query_cross_workspace_graph")
    @patch("src.backend.db.SessionLocal")
    def test_nexus_get_email_draft_context(self, mock_session_cls, mock_cross_query):
        mock_cross_query.return_value = {
            "response": "Client ACME Corp receives Net-30 payment terms.",
            "sources": [{"filename": "ACME_Contract.pdf"}]
        }

        res_json = nexus_get_email_draft_context("Payment terms inquiry", client_or_topic="ACME Corp")
        data = json.loads(res_json)
        self.assertEqual(data["status"], "success")
        self.assertIn("Net-30", data["raw_response"])
        self.assertIn("📧 Business Email Guidance", data["email_guidance"])

    @patch("src.backend.rag_engine.generate_sop_checklist")
    @patch("src.backend.db.SessionLocal")
    def test_nexus_get_sop_checklist(self, mock_session_cls, mock_gen_checklist):
        mock_gen_checklist.return_value = {
            "title": "Onboarding Checklist",
            "phases": [{"name": "Day 1", "tasks": ["Setup email", "Sign NDA"]}]
        }

        res_json = nexus_get_sop_checklist("Onboarding procedure")
        data = json.loads(res_json)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["checklist"]["title"], "Onboarding Checklist")

    @patch("src.backend.rag_engine.query_expert_finder")
    @patch("src.backend.db.SessionLocal")
    def test_nexus_find_expert(self, mock_session_cls, mock_expert_finder):
        mock_expert_finder.return_value = "Subject Matter Expert for Billing: Jane Doe (Head of Finance)"

        res_json = nexus_find_expert("Billing and Invoicing")
        data = json.loads(res_json)
        self.assertEqual(data["status"], "success")
        self.assertIn("Jane Doe", data["expert_analysis"])

if __name__ == "__main__":
    unittest.main()
