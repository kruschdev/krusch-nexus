"""
tests/unit/test_pack_exporter.py
================================
Comprehensive test suite verifying the Authority Pack Exporter & Cartridge Forge:
1. Translating parsed documents (PDF, TXT, tabular) into certified Authority Pack YAML.
2. Span grounding validation: slots anchored to exact quoted sentences and physical bounding boxes.
3. Multi-SKU conformance: Jurisdiction Pack, Standards Pack, Playbook Pack.
4. Exporting from ingested database document IDs with chunk reconstruction.
5. CLI execution and PackValidator rejection of ungrounded or oversized entries.
"""

import os
import sys
import yaml
import shutil
import tempfile
import argparse
import unittest

from krusch_nexus import (
    NexusClient,
    NexusConfig,
    DocType,
    export_authority_pack,
    extract_grounded_slots,
    AuthorityPackExporter,
    PackValidator,
    PackSku,
    PackValidationError,
    SourceSpan,
    SpanGroundedSlot
)
from krusch_nexus.cli import cmd_export_pack

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")


class TestPackExporter(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nexus_pack_test_")
        self.db_path = os.path.join(self.temp_dir, "test_pack.db")
        self.config = NexusConfig(
            database_url=f"sqlite:///{self.db_path}",
            allowed_ingest_roots=[self.temp_dir, FIXTURES_DIR],
            embed_backend="dummy"
        )
        self.client = NexusClient(self.config)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_export_from_file_jurisdiction_muni(self):
        """Verify municipal code TXT exports as a certified Jurisdiction Pack with deposit caps and notices."""
        muni_file = os.path.join(FIXTURES_DIR, "municipal_code.txt")
        yaml_out = export_authority_pack(
            muni_file,
            sku=PackSku.JURISDICTION,
            pack_id="ca_oakland_pack_v1",
            state="CA",
            municipality="Oakland"
        )
        data = yaml.safe_load(yaml_out)

        self.assertEqual(data["pack_id"], "ca_oakland_pack_v1")
        self.assertEqual(data["version"], "1.0.0")
        self.assertEqual(data["sku"], "jurisdiction")
        self.assertEqual(data["state"], "CA")
        self.assertEqual(data["municipality"], "Oakland")
        self.assertIn("statutes", data)
        self.assertGreaterEqual(len(data["statutes"]), 3)

        # Verify covered topics
        topics = data["coverage"]["covered_topics"]
        self.assertIn("Security Deposits", topics)
        self.assertIn("Rent Increases", topics)
        self.assertIn("Just Cause Evictions", topics)

        # Check § 1950.5 slot extraction & span grounding
        sec_1950 = next((s for s in data["statutes"] if "1950.5" in s["citation"]), None)
        self.assertIsNotNone(sec_1950)
        self.assertEqual(sec_1950["statutory_slots"]["deposit_cap_months"], 1.0)
        self.assertIn("deposit_cap_months", sec_1950["grounded_slots"])
        g_slot = sec_1950["grounded_slots"]["deposit_cap_months"]
        self.assertEqual(g_slot["value"], 1.0)
        self.assertEqual(g_slot["unit"], "months")
        self.assertIn("one month's rent", g_slot["source_span"]["quoted_sentence"])

        # Check § 8.22.030 written notice boolean
        sec_822 = next((s for s in data["statutes"] if "8.22.030" in s["citation"]), None)
        self.assertIsNotNone(sec_822)
        self.assertTrue(sec_822["statutory_slots"]["requires_written_warning_notice"])

        # Validate with PackValidator
        self.assertTrue(PackValidator.validate_pack_dict(data, strict_grounding=True))

    def test_export_from_pdf_with_bounding_boxes(self):
        """Verify PDF parser coordinates and bounding boxes propagate into Authority Pack grounded slots."""
        pdf_file = os.path.join(FIXTURES_DIR, "sample_contract.pdf")
        yaml_out = export_authority_pack(
            pdf_file,
            sku=PackSku.PLAYBOOK,
            pack_id="sample_lease_playbook"
        )
        data = yaml.safe_load(yaml_out)

        self.assertEqual(data["pack_id"], "sample_lease_playbook_pack_v1")
        self.assertEqual(data["sku"], "playbook")
        self.assertIn("clauses", data)

        # Find Section 19.3 Termination for Breach
        sec_19 = next((c for c in data["clauses"] if "19.3" in c["citation"]), None)
        self.assertIsNotNone(sec_19)
        self.assertEqual(sec_19["slots"]["cure_days"], 30)

        # Verify physical citation spine
        g_cure = sec_19["grounded_slots"]["cure_days"]
        self.assertEqual(g_cure["value"], 30)
        self.assertEqual(g_cure["unit"], "days")
        span = g_cure["source_span"]
        self.assertEqual(span["page_number"], 2)
        self.assertEqual(span["pdf_page"], 2)
        self.assertIsNotNone(span["bbox"])
        self.assertEqual(len(span["bbox"]), 4)
        self.assertEqual(span["bbox"][0], 50.0)  # Left margin
        self.assertIn("thirty days", span["quoted_sentence"])

        # Validate
        self.assertTrue(PackValidator.validate_pack_dict(data, strict_grounding=True))

    def test_export_from_file_playbook_license(self):
        """Verify SaaS agreement extracts Service Level and Liability Cap slots."""
        license_file = os.path.join(FIXTURES_DIR, "heldout_software_license.txt")
        yaml_out = export_authority_pack(
            license_file,
            sku=PackSku.PLAYBOOK,
            pack_id="saas_playbook_v1"
        )
        data = yaml.safe_load(yaml_out)

        self.assertEqual(data["sku"], "playbook")
        clauses = data["clauses"]

        # Section 1: node count
        c1 = next((c for c in clauses if "1." in c["citation"]), None)
        self.assertIsNotNone(c1)
        self.assertEqual(c1["slots"]["max_server_nodes"], 50)

        # Section 2: SLA availability
        c2 = next((c for c in clauses if "2." in c["citation"]), None)
        self.assertIsNotNone(c2)
        self.assertEqual(c2["slots"]["sla_availability_percent"], 99.95)

        # Section 3: Liability cap
        c3 = next((c for c in clauses if "3." in c["citation"]), None)
        self.assertIsNotNone(c3)
        self.assertEqual(c3["slots"]["liability_cap_months"], 12)

        self.assertTrue(PackValidator.validate_pack_dict(data, strict_grounding=True))

    def test_export_from_file_standards_table(self):
        """Verify multi-column financial table extracts structured table grids in Standards Pack."""
        table_pdf = os.path.join(FIXTURES_DIR, "heldout_sec_10k_table.pdf")
        yaml_out = export_authority_pack(
            table_pdf,
            sku=PackSku.STANDARDS,
            pack_id="sec_10k_standards"
        )
        data = yaml.safe_load(yaml_out)

        self.assertEqual(data["sku"], "standards")
        self.assertIn("standards", data)
        entry = data["standards"][0]
        self.assertIn("table_grids", entry)
        self.assertGreaterEqual(len(entry["table_grids"]), 1)
        tbl = entry["table_grids"][0]
        self.assertEqual(tbl["num_rows"], 5)
        self.assertEqual(tbl["num_cols"], 3)
        self.assertIn("Revenue: 2026: $84,250", tbl["markdown"])

        self.assertTrue(PackValidator.validate_pack_dict(data, strict_grounding=True))

    def test_export_from_ingested_document_id(self):
        """Verify client.export_authority_pack can reconstruct an Authority Pack from an ingested DB document."""
        muni_file = os.path.join(FIXTURES_DIR, "municipal_code.txt")
        report = self.client.ingest(muni_file, workspace="TestPackWS", doc_type=DocType.AUTHORITY)
        self.assertEqual(report.status, "completed")

        # Export via document ID
        yaml_out = self.client.export_authority_pack(
            target=report.document_id,
            workspace="TestPackWS",
            sku="jurisdiction",
            state="CA",
            municipality="Oakland"
        )
        data = yaml.safe_load(yaml_out)
        self.assertEqual(data["sku"], "jurisdiction")
        self.assertEqual(data["source_document_hash"], f"sha256:{report.file_hash}")
        self.assertTrue(PackValidator.validate_pack_dict(data, strict_grounding=True))

    def test_cli_export_pack_command(self):
        """Verify CLI execution of 'nexus export-pack' with file output."""
        out_yaml = os.path.join(self.temp_dir, "cli_pack.yaml")
        muni_file = os.path.join(FIXTURES_DIR, "municipal_code.txt")

        args = argparse.Namespace(
            target=muni_file,
            workspace=None,
            sku="jurisdiction",
            pack_id="cli_test_pack",
            publisher="City of Oakland",
            edition="2026 Code",
            description="CLI Export Test",
            domain="legal",
            state="CA",
            municipality="Oakland",
            county="Alameda",
            effective_from="2024-07-01",
            output=out_yaml,
            json=False,
            no_verify=False
        )

        code = cmd_export_pack(args)
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(out_yaml))

        with open(out_yaml, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        self.assertEqual(data["pack_id"], "cli_test_pack_pack_v1")
        self.assertEqual(data["publisher"], "City of Oakland")
        self.assertEqual(data["edition"], "2026 Code")

    def test_pack_validator_rejection_rules(self):
        """Verify PackValidator rejects malformed packs, token overruns, and ungrounded slots."""
        # 1. Missing required root keys
        with self.assertRaises(PackValidationError):
            PackValidator.validate_pack_dict({"pack_id": "test"})

        # 2. Empty covered topics
        with self.assertRaises(PackValidationError):
            PackValidator.validate_pack_dict({
                "pack_id": "test",
                "version": "1.0",
                "publisher": "Gov",
                "description": "desc",
                "coverage": {"covered_topics": []},
                "statutes": [{"citation": "§ 1", "topic": "T", "raw_content": "Text"}]
            })

        # 3. Oversized token budget
        huge_text = "word " * 1200  # ~1200 tokens > 850
        with self.assertRaises(PackValidationError):
            PackValidator.validate_pack_dict({
                "pack_id": "test",
                "version": "1.0",
                "publisher": "Gov",
                "description": "desc",
                "coverage": {"covered_topics": ["T"], "known_uncovered_topics": []},
                "statutes": [{"citation": "§ 1", "topic": "T", "raw_content": huge_text}]
            })

        # 4. Ungrounded slot value failure
        with self.assertRaises(PackValidationError):
            PackValidator.validate_pack_dict({
                "pack_id": "test",
                "version": "1.0",
                "publisher": "Gov",
                "description": "desc",
                "coverage": {"covered_topics": ["T"], "known_uncovered_topics": []},
                "statutes": [{
                    "citation": "§ 1",
                    "topic": "T",
                    "raw_content": "Tenant shall cure within thirty days.",
                    "grounded_slots": {
                        "cure_days": {
                            "value": 999,  # 999 does not appear anywhere in quote!
                            "source_span": {
                                "quoted_sentence": "Tenant shall cure within thirty days."
                            }
                        }
                    }
                }],
                "strict_grounding": True
            })

    def test_extract_grounded_slots_standalone(self):
        """Assert standalone extract_grounded_slots handles durations, caps, and percentages."""
        sample = "Under Section 1950.5(c), a landlord may not demand security in excess of one month's rent. Within 21 calendar days after vacating, return the deposit."
        stat_slots, g_slots = extract_grounded_slots(sample, page_number=1)

        self.assertEqual(stat_slots["deposit_cap_months"], 1.0)
        self.assertEqual(stat_slots["accounting_days"], 21)
        self.assertTrue(g_slots["deposit_cap_months"].verify_grounding())
        self.assertTrue(g_slots["accounting_days"].verify_grounding())


if __name__ == "__main__":
    unittest.main()
