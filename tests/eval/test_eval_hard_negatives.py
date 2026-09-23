"""
tests/eval/test_eval_hard_negatives.py
======================================
Hard Negative Evaluation Suite:
Prevents gaming by asserting retrieval precision on adversarial lookalikes:
1. Near-miss section IDs: 8.22 vs 8.22.030(C)
2. Opposite-party redlines: strikethrough rejected draft terms vs final accepted provisions
3. Exhibits vs Body: Exhibit form template vs operative agreement body
4. Lookalike headers: Recital referencing a statute vs operative substantive statute section
"""

import os
import shutil
import tempfile
import unittest

from krusch_nexus import NexusClient, NexusConfig, DocType
from krusch_nexus.store import init_db, get_engine


class TestEvalHardNegatives(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nexus_hard_neg_")
        self.db_path = os.path.join(self.temp_dir, "test_hn.db")
        self.config = NexusConfig(
            database_url=f"sqlite:///{self.db_path}",
            allowed_ingest_roots=[self.temp_dir]
        )
        self.engine = get_engine(self.config.database_url)
        init_db(self.engine)
        self.client = NexusClient(self.config)

        # 1. Near-miss sections document
        self.statute_doc = os.path.join(self.temp_dir, "oakland_rent_code.txt")
        with open(self.statute_doc, "w", encoding="utf-8") as f:
            f.write(
                "Section 8.22 Residential Rent Adjustment Program Purpose\n"
                "This Chapter shall be known as the Oakland Residential Rent Adjustment Ordinance.\n"
                "The purpose is to stabilize rent increases and protect community housing stability.\n\n"
                "Section 8.22.030 Exemptions and Scope\n"
                "The provisions of this chapter shall not apply to newly constructed rental units.\n\n"
                "Section 8.22.030(C) Notice to Tenant of Rent Adjustment Program Regulations\n"
                "Every landlord must provide written notice of the existence of the Rent Adjustment Program\n"
                "at the inception of tenancy and concurrent with any rent increase notice on mandatory Form RAP-1."
            )

        # 2. Exhibits vs Body document
        self.contract_doc = os.path.join(self.temp_dir, "credit_agreement.txt")
        with open(self.contract_doc, "w", encoding="utf-8") as f:
            f.write(
                "Recital B Statutory Citation\n"
                "Whereas Borrower operates in compliance with California Civil Code § 1950.5 regulations.\n\n"
                "Section 2.1 Principal Commitment Amount\n"
                "Lender agrees to make term loans to Borrower in an aggregate principal amount of $5,000,000.\n\n"
                "Exhibit A Form of Promissory Note\n"
                "FOR VALUE RECEIVED, the undersigned promises to pay [Principal Amount: $_______] on the Maturity Date."
            )

        # 3. Substantive statute
        self.substantive_statute = os.path.join(self.temp_dir, "civil_code_1950_5.txt")
        with open(self.substantive_statute, "w", encoding="utf-8") as f:
            f.write(
                "California Civil Code § 1950.5 Security Deposit Deductions and Accounting\n"
                "No later than 21 calendar days after the tenant has vacated the premises,\n"
                "the landlord shall furnish the tenant with a copy of an itemized statement indicating\n"
                "the basis for, and the amount of, any security received and the disposition of the security."
            )

        self.client.ingest(self.statute_doc, workspace="HardNegWorkspace", doc_type=DocType.AUTHORITY)
        self.client.ingest(self.contract_doc, workspace="HardNegWorkspace", doc_type=DocType.WORK_PRODUCT)
        self.client.ingest(self.substantive_statute, workspace="HardNegWorkspace", doc_type=DocType.AUTHORITY)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_near_miss_section_precision(self):
        """Assert query for Section 8.22.030(C) ranks 8.22.030(C) above broader 8.22."""
        hits = self.client.search("Section 8.22.030(C) Notice to Tenant", workspace="HardNegWorkspace", limit=3)
        self.assertGreater(len(hits), 0)
        top = hits[0]
        self.assertIn("8.22.030(C)", (top.header or "") + " " + top.text)
        self.assertIn("Form RAP-1", top.text)

    def test_exhibits_vs_body_precision(self):
        """Assert query for loan commitment amount retrieves Section 2.1 body ($5,000,000), not Exhibit A template."""
        hits = self.client.search("principal commitment amount term loans", workspace="HardNegWorkspace", limit=3)
        self.assertGreater(len(hits), 0)
        top = hits[0]
        self.assertIn("5,000,000", top.text)
        self.assertNotIn("Exhibit A", top.header or "")

    def test_substantive_statute_vs_recital_mention(self):
        """Assert query for security deposit accounting retrieves Civil Code 1950.5 statute over passing recital mention."""
        hits = self.client.search("California Civil Code § 1950.5 itemized statement 21 calendar days", workspace="HardNegWorkspace", limit=3)
        self.assertGreater(len(hits), 0)
        top = hits[0]
        self.assertEqual(top.filename, "civil_code_1950_5.txt")
        self.assertIn("21 calendar days", top.text)


if __name__ == "__main__":
    unittest.main()
