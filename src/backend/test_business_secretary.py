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
from src.backend.db import Base, Employee, Workspace, Document, GraphNode, GraphEdge

class TestBusinessSecretary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create in-memory SQLite DB
        cls.engine = create_engine("sqlite:///:memory:")
        cls.Session = sessionmaker(bind=cls.engine)
        Base.metadata.create_all(cls.engine)
        cls.db = cls.Session()

        # Add a test workspace
        cls.workspace = Workspace(id=1, name="test_corp_ws", description="Test Workspace")
        cls.db.add(cls.workspace)

        # Add a test employee
        cls.employee = Employee(
            id=1,
            provider="local",
            external_id="emp_001",
            email="sarah.connor@example.com",
            first_name="Sarah",
            last_name="Connor",
            display_name="Sarah Connor",
            job_title="Operations Director",
            department="Operations",
            status="active"
        )
        cls.db.add(cls.employee)
        cls.db.commit()

        # Write a mock business profile file for testing
        from src.backend.pocketlawyer.business_profile_manager import PROFILE_DIR, save_profile
        os.makedirs(PROFILE_DIR, exist_ok=True)
        cls.test_profile = {
            "owner": {
                "name": "Miles Dyson",
                "title": "Lead Researcher",
                "email": "miles.dyson@cyberdyne.com",
                "phone": "555-1234"
            },
            "company": {
                "name": "Cyberdyne Systems",
                "entity_type": "C-Corp",
                "ein": "12-3456789",
                "state_of_formation": "California",
                "formation_date": "2026-01-01",
                "dba": "Cyberdyne",
                "description": "Neural net processors and autonomous robotics"
            },
            "address": {
                "street": "123 Research Way",
                "city": "Sunnyvale",
                "state": "California",
                "zip": "94089"
            },
            "industry": {
                "primary": "Defense Research",
                "naics_code": "541715"
            },
            "employees": {
                "count": 50,
                "has_contractors": False,
                "contractor_count": 0
            },
            "compliance": {
                "business_license": True,
                "license_expiration": "2027-01-01",
                "insurance_types": ["General Liability", "Cyber Liability"]
            },
            "preferences": {
                "ai_formality": "professional"
            }
        }
        save_profile(user_id=1, profile_data=cls.test_profile)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        # Clean up mock profile
        from src.backend.pocketlawyer.business_profile_manager import delete_profile
        delete_profile(user_id=1)

    def test_guardrail_router_triggering(self):
        """Verify that high-risk keywords trigger the UPL shield disclaimers."""
        from src.backend.pocketlawyer.guardrail_router import check_guardrails
        
        # Test low risk
        triggered, matches, injection = check_guardrails("How do I write a comment in Python?")
        self.assertFalse(triggered)
        self.assertIsNone(injection)

        # Test critical risk (pregnancy & firing)
        triggered, matches, injection = check_guardrails("Should I fire my pregnant developer?")
        self.assertTrue(triggered)
        self.assertIn("fire", matches)
        self.assertIn("pregnant", matches)
        self.assertIn("CRITICAL-RISK", injection)

        # Test high risk (sue/lawsuit)
        triggered, matches, injection = check_guardrails("I want to sue a contractor for unpaid work.")
        self.assertTrue(triggered)
        self.assertIn("sue", matches)
        self.assertIn("ELEVATED LEGAL QUERY", injection)

    @patch('src.backend.rag_engine.SessionLocal')
    @patch('src.backend.rag_engine.llm_fast')
    @patch('src.backend.rag_engine.llm_reasoning')
    def test_smart_query_upl_programmatic_prepending(self, mock_reasoning, mock_fast, mock_session):
        """Verify that a query triggering critical guardrails programmatically prepends the warning disclaimer."""
        mock_session.return_value = self.db
        
        # Mock LLM outputs
        mock_reasoning.complete.return_value = "You should explain FEHA pregnancy protections."
        mock_fast.complete.return_value = '{"tool": "none", "params": {}}'
        
        # Mock the vector store query pipeline responses
        with patch('src.backend.rag_engine.query_with_audit_trail') as mock_rag:
            mock_rag.return_value = {
                "claim": "Under FEHA, pregnancy discrimination is illegal.",
                "logic": "1. FEHA covers employers with 5+ employees.\n2. Sarah is pregnant.",
                "sources": []
            }
            with patch('src.backend.rag_engine.agentic_proxy_route') as mock_route:
                mock_route.return_value = "RAG"
                
                # Execute query that triggers critical guardrails
                res = rag_engine.smart_query("Should I fire Sarah Connor who is pregnant?", workspace_id=1)
                
                self.assertIn("response", res)
                response_text = res["response"]
                self.assertIn("CRITICAL WARNING", response_text)
                self.assertIn("Under FEHA, pregnancy discrimination is illegal.", response_text)

    def test_business_profile_context_relevance(self):
        """Verify that relevant profile sections are fetched based on keywords."""
        from src.backend.pocketlawyer.business_profile_manager import get_relevant_context
        
        # Address/Location query
        context = get_relevant_context(user_id=1, message="Where is the company address?")
        self.assertIn("[LOCATION]", context)
        self.assertNotIn("[TEAM]", context)

        # Team query
        context2 = get_relevant_context(user_id=1, message="How many employees do we have?")
        self.assertIn("[TEAM]", context2)
        self.assertNotIn("[LOCATION]", context2)

    @patch('src.backend.rag_engine.SessionLocal')
    @patch('src.backend.rag_engine.llm_fast')
    def test_business_tool_route_compliance_calendar(self, mock_fast, mock_session):
        """Verify that a compliance calendar query routes to the calendar tool and returns a proposed action first."""
        mock_session.return_value = self.db
        
        # Mock LLM classifying the query as compliance_calendar without parameters
        mock_fast.complete.return_value = '{"tool": "compliance_calendar", "params": {}}'
        
        with patch('src.backend.rag_engine.agentic_proxy_route') as mock_route:
            mock_route.return_value = "BUSINESS_TOOL"
            
            res = rag_engine.smart_query("Generate my company filing calendar deadlines", workspace_id=1)
            self.assertIn("proposed_action", res)
            self.assertEqual(res["proposed_action"]["tool"], "compliance_calendar")

    @patch('src.backend.rag_engine.SessionLocal')
    @patch('src.backend.rag_engine.llm_reasoning')
    def test_confirmation_first_flow(self, mock_reasoning, mock_session):
        """Verify that passing approved tool params directly runs the tool without proposing again."""
        mock_session.return_value = self.db
        mock_reasoning.complete.return_value = "Here are the compliance calendar deadlines for Cyberdyne Systems."
        
        res = rag_engine.smart_query(
            "Generate my company filing calendar deadlines",
            workspace_id=1,
            approved_tool="compliance_calendar",
            approved_params={"entity_type": "C-Corp", "inception_date": "2026-01-01"}
        )
        self.assertNotIn("proposed_action", res)
        self.assertIn("response", res)
        self.assertIn("Cyberdyne Systems", res["response"])

    @patch('src.backend.rag_engine.SessionLocal')
    @patch('src.backend.rag_engine.llm_reasoning')
    def test_employee_briefing(self, mock_reasoning, mock_session):
        """Verify that employee daily briefing queries SQLite DB items and synthesizes briefing."""
        mock_session.return_value = self.db
        mock_reasoning.complete.return_value = "Briefing: 1 document uploaded."
        
        from src.backend.rag_engine import generate_employee_briefing
        res = generate_employee_briefing(workspace_id=1, lookback_hours=24)
        self.assertIn("Briefing:", res)

    @patch('src.backend.rag_engine.llm_fast')
    @patch('src.backend.rag_engine.VectorStoreIndex')
    def test_sop_checklist(self, mock_index, mock_fast):
        """Verify that SOP checklists query the vector store and return structured phases and tasks."""
        mock_fast.complete.return_value = json.dumps({
            "title": "Deploy Hotfix",
            "phases": [
                {
                    "name": "Phase 1: Preparation",
                    "tasks": ["Run tests locally", "Check branch status"]
                }
            ]
        })
        
        from src.backend.rag_engine import generate_sop_checklist
        res = generate_sop_checklist(workspace_id=1, query="deploy hotfix")
        self.assertEqual(res["title"], "Deploy Hotfix")
        self.assertEqual(len(res["phases"]), 1)

    @patch('src.backend.rag_engine.llm_fast')
    @patch('src.backend.rag_engine.VectorStoreIndex')
    def test_code_snippet_companion(self, mock_index, mock_fast):
        """Verify that the snippet companion queries code documents and formats copyable snippets."""
        mock_fast.complete.return_value = json.dumps({
            "title": "Postgres Connection",
            "language": "python",
            "code": "db = SessionLocal()",
            "explanation": "Ensure session is closed after execution."
        })
        
        from src.backend.rag_engine import generate_code_snippet
        res = generate_code_snippet(workspace_id=1, query="postgres database session")
        self.assertEqual(res["title"], "Postgres Connection")
        self.assertEqual(res["language"], "python")
        self.assertIn("db =", res["code"])

    @patch('requests.post')
    def test_local_vision_ocr_fallback_and_ollama(self, mock_post):
        """Verify that local vision OCR encodes images and makes standard Ollama chat calls."""
        import json
        # 1. Mock successful Ollama response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {
                "content": json.dumps({
                    "merchant": "Office Depot",
                    "date": "2026-05-20",
                    "total": 85.50,
                    "currency": "USD",
                    "items": [{"name": "A4 Paper", "price": 85.50}],
                    "confidence": 0.95,
                    "classification": "office_supplies"
                })
            }
        }
        mock_post.return_value = mock_response
        
        from src.backend.rag_engine import run_local_vision_ocr
        res = run_local_vision_ocr(b"fake-image-bytes", "image/png")
        self.assertEqual(res["merchant"], "Office Depot")
        self.assertEqual(res["total"], 85.50)
        
        # 2. Mock Ollama failure to test OCR simulation fallback
        mock_post.side_effect = Exception("Connection refused")
        res_fallback = run_local_vision_ocr(b"fake-image-bytes", "image/png")
        self.assertEqual(res_fallback["merchant"], "Simulated Merchant")
        self.assertIn("fallback", res_fallback["note"])

if __name__ == "__main__":
    unittest.main()


