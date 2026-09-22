# GEMINI_INFLIGHT — Krusch-Nexus

> Last updated: 2026-07-28 (Document Ingestion UI Polished & Deployed)

## Active Environment & Nodes
- Primary target: `kruschserv` (10.0.0.85)
- Public Domain: `https://krusch.dev/nexus`
- Vector Backend: `Polygres.com` Cloud pgvector (`NEXUS_DB_TARGET=polygres`) or local PostgreSQL
- AI Tagging & Embedding: OpenRouter (`qwen/qwen-2.5-coder-32b-instruct` & `baai/bge-large-en-v1.5`) with automatic local Ollama failover
- MCP Interfaces: Stdio launcher (`scripts/run_mcp.sh`) + Network SSE Server (`http://10.0.0.85:8002/sse`)
- Ingestion Portal: `https://krusch.dev/ingest`

## Recent Session Milestone: Document Ingestion UI Fixes & Edge Polish
- **UI Button Layout Un-broken**: Resolved global CSS button width/border-radius pollution. Styled `🔑 Sync Google Workspace` and document catalog action buttons (**Classify**, **Flag**, **Delete**) into crisp rectangular pill buttons.
- **RAG Verification Panel**: Integrated `<div id="chat-history"></div>` into a dedicated RAG query response panel with dark glassmorphism, typing indicators, user/agent bubbles, and source tag rendering.
- **Sync Routing & Cloudflare Edge Deployment**: Added `/api/sync/google-workspace` API route and provider normalization. Rebuilt and deployed to Cloudflare Workers edge via `./scripts/deploy_cloudflare.sh`.


### Core Beta Capabilities Shipped
1. **Business RAG & Institutional Knowledge MCP Server Suite**:
   - `nexus_query_business_knowledge`: Hybrid vector + GraphRAG search over SOPs, financial reports, emails, policies.
   - `nexus_get_email_draft_context`: Formats company guidelines & client context for AI email drafting.
   - `nexus_ingest_business_document`: Ingest documents/SOPs/emails directly via MCP tool call.
   - `nexus_get_sop_checklist`: Extracts structured operational action items from SOPs.
   - `nexus_find_expert`: Identifies internal team SMEs and role owners.
   - `nexus_sync_google_workspace`: 1-click synchronization of Gmail threads, Google Drive folders, Docs, Sheets, and Slides.
   - `nexus_list_workspaces` & `nexus_list_documents`: Explores knowledge spaces and document catalogs.
   - **`krusch-context-mcp` Integration**: Direct connection between Krusch-Nexus agent client (`src/agent/mcp-client.js`) and `krusch-context-mcp` for unified 1024d `bge-large` vector retrieval, episodic memory, and GraphRAG routing.

2. **Official Open Beta Web & Frontend Suite (`krusch.dev/nexus`)**:
   - **Landing Page (`GET /nexus`)**: Dark-mode glassmorphic landing page showcasing feature cards, pricing tiers, and instant registration.
   - **Interactive Setup Docs (`GET /nexus/docs`)**: Tabbed documentation page with 1-click copyable config snippets for Claude Desktop, Cursor, Antigravity, OpenClaw, and SSE Network mode.
   - **Machine-Readable Agent Specification (`GET /nexus/agent-setup.json`)**: Machine-readable JSON spec allowing web-browsing AI agents to configure Krusch-Nexus automatically.
   - **Dedicated Ingestion Portal (`GET /chat`)**: Drag-and-drop file uploader, email/SOP text pasting, Google Workspace 1-click sync, and live document catalog.

3. **Zero-Stripe Frictionless Signup & License Key Authentication**:
   - `POST /api/signup`: Programmatic registration issuing private **Nexus License Keys** (`nx_live_...`).
   - **Free Pro Beta Special ($0/mo)**: Assigns 2,500 Ingested Documents and 25,000 Monthly RAG Queries with zero payment friction.

4. **Zero-Knowledge Encryption & Cloud Integration**:
   - Zero-Knowledge client-side AES-256 payload encryption (`crypto_utils.py`) ensuring document ciphertext in Polygres Cloud is unreadable to cloud hosts.
   - Polygres Cloud live connection (`POLYGRES_URL`).

5. **Proactive Error Telemetry & Self-Healing Automation**:
   - `POST /api/telemetry/event` and `GET /api/telemetry/status`: Real-time diagnostic telemetry and health score calculation.
   - Automatic failover for OpenRouter rate limits -> local GPU Ollama models (`qwen2.5-coder` & `bge-large`).
   - `POST /api/feedback`: In-app feedback & bug report gateway logging to `beta_feedback.jsonl`.

6. **Comprehensive 100% Test Verification**:
   - **39 unit test cases passed 100% cleanly across all 12 backend test suites**:
     - `test_e2e_signup_audit` (2/2)
     - `test_telemetry` (3/3)
     - `test_feedback_api` (1/1)
     - `test_agent_accessibility` (1/1)
     - `test_landing_signup` (2/2)
     - `test_google_workspace` (3/3)
     - `test_zero_knowledge_crypto` (3/3)
     - `test_license_monetization` (5/5)
     - `test_monetization_guardrails` (3/3)
     - `test_mcp` (6/6)
     - `test_sync` (6/6)
     - `test_nexus_integration` (4/4)

## Next Steps for Open Beta Release
1. Deploy containers via `docker compose up -d` on `kruschserv`.
2. Share `https://krusch.dev/nexus` for user signup and `docs/AGENT_SETUP_GUIDE.md` for AI agent self-configuration.
