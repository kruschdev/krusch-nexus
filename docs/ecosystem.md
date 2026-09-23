# Krusch Homelab Ecosystem Context

This document captures the homelab context and upstream consumer relationship for KruschNexus.

## Role in the Fleet

KruschNexus serves as the shared, air-gapped document ingestion engine and citation spine across the kruschDev homelab infrastructure:

- **kruschserv** (Primary Host): Runs the PostgreSQL/pgvector database container (`krusch_nexus_db`), the FastAPI REST API, and the background ingestion watch daemon (`nexus-daemon`).
- **kruschgame** (Ollama GPU Inference): Hosts local embedding models (`bge-large`, 1024 dims) for high-throughput zero-cloud vector generation.
- **kruschdev / Workstations**: Connect to KruschNexus via the Model Context Protocol (FastMCP) or the Python SDK (`from krusch_nexus import NexusClient`).

## Domain Consumers

Domain applications consume KruschNexus strictly via its public client or MCP surface:

1. **PocketLawyer / KruschLaw**:
   - Legal research, municipal code exploration, and statutory compliance.
   - Requires page-true citations (`contract.pdf p.3 Section 8.22 Permitted Use`) to prevent legal hallucinations.
   - Enforces matter isolation (`workspace_id`) across distinct client matters.

2. **KruschBiz / Operational Tools**:
   - Vendor matrix analysis, policy manuals, corporate records retention schedules.
   - Consumes structural locators (`memo.docx Section 1.2 > Backup Retention`) and table row citations.

## Upstream Boundary Rules

- **Zero UI in Ingestion Core**: No frontend or web chat interfaces live in `krusch-nexus`.
- **Zero Agent Handoff Logic**: Domain prompting, legal drafting, and agentic reasoning remain in consumer projects.
- **Strict Versioned SDK**: All consumers import from `krusch_nexus` (`from krusch_nexus import NexusClient, SearchHit, IngestReport, DocType`), never internal modules.
