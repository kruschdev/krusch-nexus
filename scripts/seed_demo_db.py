#!/usr/bin/env python3
"""
scripts/seed_demo_db.py
=======================
Deterministically seeds the static SQLite demo database fixture: `data/demo.db`.
Includes:
- LegalCorpus workspace with commercial contract, municipal ordinance, and work product memo
- LitigationHold workspace under active legal hold with evidentiary records and immutable audit logs
- Fully populated chunks, FTS index, and deterministic unit embeddings for instant offline execution
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from krusch_nexus import NexusConfig, NexusClient, DocType
from krusch_nexus.store import init_db, get_engine


def seed_demo_database():
    data_dir = os.path.join(PROJECT_ROOT, "data")
    os.makedirs(data_dir, exist_ok=True)
    db_path = os.path.join(data_dir, "demo.db")

    if os.path.exists(db_path):
        os.remove(db_path)

    cfg = NexusConfig(
        database_url=f"sqlite:///{db_path}",
        embed_backend="dummy",
        embed_dim=1024,
        allowed_ingest_roots=[data_dir]
    )
    engine = get_engine(cfg.database_url)
    init_db(engine)
    client = NexusClient(cfg)

    # 1. Commercial Agreement
    msa_text = """MASTER SERVICES AGREEMENT

ARTICLE I: RECITALS
This Master Services Agreement ("Agreement") is effective as of January 15, 2026.
Provider: CloudScale Enterprise Solutions LLC.
Client: Acme Logistics Corporation.

ARTICLE IV: FINANCIAL TERMS
Section 4.1 Invoicing
Invoices shall be rendered monthly in arrears with itemized service records.

Section 4.2 Payment Terms
Client shall pay all undisputed invoiced amounts within thirty (30) days of receipt. Late payments accrue interest at 1.5% per month.

ARTICLE IX: LIMITATION OF LIABILITY
Section 9.1 Aggregate Cap
Except for indemnification obligations and breaches of confidentiality, neither party's aggregate liability under this agreement shall exceed $1,000,000 or the total fees paid in the preceding twelve months.

Section 9.2 Consequential Damages Waiver
Neither party shall be liable for indirect, incidental, or punitive damages.
"""
    doc1 = os.path.join(data_dir, "msa_commercial.txt")
    with open(doc1, "w", encoding="utf-8") as f:
        f.write(msa_text)

    r1 = client.ingest(doc1, workspace="LegalCorpus", doc_type=DocType.AUTHORITY)
    print(f"Ingested Commercial MSA: doc_id={r1.document_id}, chunks={r1.total_chunks}")

    # 2. Municipal Ordinance
    ord_text = """MUNICIPAL CODE: HOUSING & TENANCY

CHAPTER 14: RENT STABILIZATION AND TENANT PROTECTIONS
Section 14.08.010 Maximum Allowable Rent Increase
Annual allowable rent increases are capped at 60% of the local Consumer Price Index (CPI) or 3.0%, whichever is lower. Landlords must give 60 days advance written notice.

Section 14.08.050 Security Deposits and Escrow
Landlords may not demand a security deposit exceeding one month's rent for unfurnished residential units. The full deposit must be returned within 21 days of tenancy termination, accompanied by itemized deduction receipts.
"""
    doc2 = os.path.join(data_dir, "municipal_ordinance.txt")
    with open(doc2, "w", encoding="utf-8") as f:
        f.write(ord_text)

    r2 = client.ingest(doc2, workspace="LegalCorpus", doc_type=DocType.AUTHORITY)
    print(f"Ingested Ordinance: doc_id={r2.document_id}, chunks={r2.total_chunks}")

    # 3. Work Product Defense Memo
    memo_text = """ATTORNEY WORK PRODUCT // CONFIDENTIAL
Matter: Acme Logistics Subcontract Audit
Author: General Counsel Office
Date: March 1, 2026

MEMORANDUM ON FORCE MAJEURE AND STATUTORY DEFENSES
Preliminary review of subcontract provisions indicates that supplier non-performance under Section 4.2 is excusable if direct government embargo or rail carrier stoppage occurs.
Recommend reserving rights under statutory good faith covenants.
"""
    doc3 = os.path.join(data_dir, "tenant_defense_memo.txt")
    with open(doc3, "w", encoding="utf-8") as f:
        f.write(memo_text)

    r3 = client.ingest(doc3, workspace="LegalCorpus", doc_type=DocType.WORK_PRODUCT)
    print(f"Ingested Defense Memo: doc_id={r3.document_id}, chunks={r3.total_chunks}")

    # 4. Evidentiary Document under Legal Hold
    subpoena_text = """SUBPOENA DUCES TECUM & EVIDENCE PRESERVATION ORDER
United States District Court, Central District of California
Matter: Civil Litigation Docket No. 2026-CV-09412
Acme Logistics Corp. v. Beta Global Logistics Inc.

ORDER FOR PRESERVATION OF ALL DISCOVERY MATERIALS
All parties, officers, and custodians are strictly ordered to preserve all communications, transactional invoices, server logs, and software manifests. Modification, deletion, or shredding of records is punishable as contempt of court.
"""
    doc4 = os.path.join(data_dir, "subpoena_preservation_order.txt")
    with open(doc4, "w", encoding="utf-8") as f:
        f.write(subpoena_text)

    r4 = client.ingest(doc4, workspace="LitigationHold", doc_type=DocType.AUTHORITY)
    client.set_legal_hold("LitigationHold", legal_hold=True)
    print(f"Ingested Subpoena Order: doc_id={r4.document_id}, chunks={r4.total_chunks} [LEGAL HOLD ACTIVATED]")

    # Clean up temp source files
    for p in [doc1, doc2, doc3, doc4]:
        if os.path.exists(p):
            os.remove(p)

    file_size = os.path.getsize(db_path)
    print(f"✅ Static SQLite demo fixture generated at {db_path} ({file_size} bytes)")


if __name__ == "__main__":
    seed_demo_database()
