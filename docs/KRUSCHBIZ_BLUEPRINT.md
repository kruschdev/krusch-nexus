# 🏢 KruschBiz: Architecture & System Blueprint

> **Private Confidential Business Intelligence & Executive Operations Engine**  
> *Air-gapped business document intelligence, executive workflows, and operational SOP synthesis using on-premise open-weight models.*

---

## 1. System Vision & Product Boundaries

In the Krusch homelab ecosystem, **KruschNexus** provides the foundational document ingestion and indexing pipeline. Specialized vertical applications consume this engine according to their distinct domain requirements:

```
                                  ┌────────────────────────────────────────────────────────┐
                                  │                      KruschNexus                       │
                                  │   (Universal Closed-Loop Document Ingestion Engine)     │
                                  │  - Multi-Format Parsers (PDF + Local OCR, DOCX, EML)    │
                                  │  - Structural Chunking (§, Headings, Deduplication)    │
                                  │  - Safe Watch-Folder Archival (.ingested/)             │
                                  │  - Local Embeddings (bge-large) & Hybrid PostgreSQL    │
                                  └───────────┬────────────────────────────────┬───────────┘
                                              │                                │
                                              ▼                                ▼
                        ┌──────────────────────────────┐ ┌──────────────────────────────────┐
                        │          KruschLaw           │ │            KruschBiz             │
                        │  (Legal Vertical)            │ │  (Business Vertical)            │
                        │ - Attorney-Client Privilege  │ │ - Confidential Corporate Data    │
                        │ - Municipal Codes & Case Law │ │ - P&L, Financials & Audit Memos  │
                        │ - Court Exhibits & Discovery │ │ - Operational SOP Action Lists   │
                        │ - 4-Part Legal Brief Format  │ │ - Policy-Compliant Email Drafts  │
                        │ - Statutory Citation Scans   │ │ - Role Owners & SME Discovery    │
                        └──────────────────────────────┘ └──────────────────────────────────┘
```

**KruschBiz** is explicitly designed for private corporate and operational knowledge. Unlike public cloud LLMs that risk leaking trade secrets, financial projections, and personnel data to third-party providers, KruschBiz runs strictly within a private, on-premise perimeter.

---

## 2. Ingest Corpus & Target Datasets

KruschBiz ingests corporate documents through the `NexusIngestClient`:

| Category | Typical Formats | Target Information Extracted |
| :--- | :--- | :--- |
| **Financials & Accounting** | PDF, CSV, XLSX, JSON | Income statements, balance sheets, cash burn, vendor invoices, tax filings |
| **Operational SOPs** | DOCX, MD, PDF | Standard operating procedures, safety checklists, deployment runbooks |
| **Contracts & Procurement** | PDF, DOCX | Master service agreements, vendor pricing, client SOWs, SLA obligations |
| **Governance & Executive** | PDF, MD | Board meeting minutes, shareholder resolutions, investor decks, strategy memos |
| **HR & People Operations** | PDF, DOCX | Employee handbooks, onboarding guides, remote work policies, equity rules |
| **Client Communications** | EML, TXT | Inbound client requests, past negotiation threads, quote approvals |

---

## 3. Specialized Business AI Capabilities

KruschBiz provides dedicated business intelligence tools (staged in `src/backend/biz_rag_staging.py`):

### 1. SOP Action Checklist Engine
- Extracts clear, actionable, numbered check items from long-form operational procedures.
- Organizes steps by phase, responsible role, and verification criteria.
- Prevents procedural drift and ensures operational compliance.

### 2. Policy-Compliant Email Drafter
- Formats corporate guidelines, pricing rules, and precedent threads into ready-to-send draft responses.
- Ensures customer-facing messages adhere to company policy without hallucinations.

### 3. Subject-Matter Expert (SME) & Role Router
- Identifies who owns a particular domain, document, or client account.
- Scans authorship metadata and role mentions to route internal inquiries to the right person.

### 4. Tabular Financial KPI & Variance Scanner
- Extracts multi-column financial tables and compares sequential quarters/months.
- Flags line-item variances (e.g., > 15% delta in cloud infrastructure spend or vendor retainer increases).

---

## 4. Technical Integration Specification

KruschBiz integrates directly with KruschNexus via Python SDK:

```python
from krusch_nexus.client import NexusIngestClient

# 1. Ingest confidential financial statement into private business workspace
client = NexusIngestClient()
report = client.ingest_file(
    filepath="/corporate/finance/2026_Q2_Financial_Statement.pdf",
    workspace="Executive_Finance",
    doc_type="authority"
)

# 2. Perform hybrid search with exact page provenance
results = client.search_corpus(
    query="operating cash flow and EBITDA margin",
    workspace="Executive_Finance",
    limit=5
)

# 3. Grounded citation output
# Results include: [2026_Q2_Financial_Statement.pdf, p. 4, § Cash Flow Analysis]
```

---

## 5. Security & Isolation Standard

- **Loopback Enforcement**: REST/FastAPI endpoints bind strictly to `127.0.0.1`.
- **Zero Cloud Leakage**: Local Ollama inference (`qwen2.5:14b` / `qwen2.5-coder:7b`) and local BGE-Large embeddings.
- **Tenant & Workspace Scoping**: Business queries are strictly restricted to designated workspaces (`Executive_Finance`, `Operations_SOP`, `HR_Confidential`).
