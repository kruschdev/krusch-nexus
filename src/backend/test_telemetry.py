import os
import sys
import unittest
from unittest.mock import MagicMock

# Set Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import src.backend.test_stubs

from src.backend.telemetry import log_system_event, record_error, run_self_healing_routine, get_telemetry_status

class TestTelemetry(unittest.TestCase):

    def test_log_system_event(self):
        evt = log_system_event("TEST_EVENT", {"module": "unit_test"}, level="INFO")
        self.assertEqual(evt["event_type"], "TEST_EVENT")
        self.assertEqual(evt["level"], "INFO")

    def test_self_healing_routine_rate_limit(self):
        result = run_self_healing_routine("OpenRouter_429_RateLimit", {"endpoint": "tagging"})
        self.assertTrue(result["healed"])
        self.assertIn("Ollama", result["action"])

    def test_record_error_and_status(self):
        record_error("ParserError", "Traceback line 1", {"file": "sample.pdf"})
        status = get_telemetry_status()
        self.assertIn(status["status"], ["OPTIMAL", "HEALTHY", "DEGRADED"])
        self.assertGreaterEqual(status["total_events_logged"], 1)

if __name__ == '__main__':
    unittest.main()
