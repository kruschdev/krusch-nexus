# Krusch-Nexus (Institutional Knowledge)

> **Status**: Active Deployment (Phase 4)
> **Last updated**: 2026-05-13

## Architecture

A cloud-native enterprise Institutional Knowledge product powered by OpenRouter Cloud AI and Polygres Cloud pgvector.
- **Backend**: Python, FastAPI, LlamaIndex, Docling
- **Database**: Polygres Cloud PostgreSQL with `pgvector` (1024d dense embeddings) & relational GraphRAG tables (`GraphNode`/`GraphEdge`)
- **Frontend**: Streamlit + Glassmorphic Landing Portal (`krusch.dev/nexus`)
- **Cloud AI / LLM**: OpenRouter (`qwen/qwen-2.5-coder-32b-instruct` for reasoning/tagging, `baai/bge-large-en-v1.5` for 1024d embeddings) operating 100% cloud-native.

## Deployment & Serverless Cloud Options

- **Cloud-Native Edge Default (Cloudflare Workers)**: Serverless edge deployment via Cloudflare Workers using `wrangler.toml` and `src/worker.js`. Runs 100% cloud-native without dependency on local hardware during server relocation:
  ```bash
  ./scripts/deploy_cloudflare.sh
  ```
- **Container Deployment (`kruschserv`)**: Local host container target `docker-compose` on `kruschserv` / `krusch.dev`. Backend binds to port 8001 (`0.0.0.0:8001->8000/tcp`).

## Cross-Workspace GraphRAG

Phase 3 introduced cross-workspace relational queries:
- We rely on SQL to fetch nodes and edges across workspaces and feed them into the `qwen2.5:7b` context window.
- Future enhancements might include light TF-IDF filtering if the relational graph exceeds the 32k context limit.

## GitHub Repository Ingestion

Nexus can ingest entire GitHub repositories as searchable knowledge workspaces:
- **Pipeline**: `ingest_graph.py` — LangGraph `StateGraph` pipeline (clone → chunk → LLM tag → index)
- **Scale**: Handles massive codebases without vector context-length limits or overwhelming GraphRAG limits.
- **Tagging**: `qwen2.5-coder:1.5b` on kruschgame (RTX 3050) generates per-file summaries and categorical tags
- **API**: `POST /api/ingest-repo` with `{ repo_url, workspace_name?, generate_tags?, max_files? }`
- **CLI**: `python -m src.backend.ingest_repo_cli https://github.com/org/repo`
- Repos are stored in workspaces prefixed `repo:` (e.g., `repo:langchainjs`)

## Testing & Workflows

Follow the standard homelab lifecycle:
- Run `./scripts/continue.sh` at session start to load in-flight state and audit active node connections.
- Run `./scripts/close.sh` before finishing to execute unit tests, audit workspace hygiene, verify `GEMINI_INFLIGHT.md`, and close cleanly.
- Use `/continue` to resume work.
- Use `/close` to document state in `GEMINI_INFLIGHT.md`.
- Use `/deploy` or manual `docker-compose` to run the stack.

## Directory Synchronization & Mobile Webhook Chat

- **Directory Caching**: User profiles are stored in the local `employees` database table. Synchronizations are adapter-based (`BaseSyncProvider` interface), supporting `LocalJsonDirectoryProvider` (for offline, mock testing) and `GoogleWorkspaceDirectoryProvider` (Admin SDK Directory & Gmail).
- **Inbound Webhook**: Real-time emails can be pushed and ingested immediately via `POST /api/sync/webhook/email` to trigger vector indexing.
- **Mobile Client**: A lightweight, vanilla HTML/CSS/JS dark-mode mobile chat client is served on `GET /chat` for private, local phone querying.

## Offline Unit Testing Constraints

- Since docker socket `/var/run/docker.sock` access is restricted on dev nodes, run unit tests directly on host using `mcp_env/bin/python -m unittest src.backend.test_sync`.
- To avoid package import errors (`fastapi`, `llama_index`) and database timeouts on dev nodes, implement local mock stubs in the `sys.modules` registry at the top of the test suite.

## Context Isolation & Workspace Boundaries

- **Workspace Scope**: The scope of this workspace is strictly limited to **Krusch-Nexus**.
- **Context Pollution Prevention**: Do not search for, reference, or mix logic with external client applications or browser active states (e.g., Compass Concierge). The continue protocol, session logs, and agent memory must remain strictly focused on the local Nexus architecture.

## 🛡️ Zero-Trust Security & Workspace Isolation Protocol

All AI Agents and backend routines in Krusch-Nexus MUST enforce the following Zero-Trust security rules without exception:

1. **Strict Tenant & Workspace Isolation**:
   - Every document query, vector search, or GraphRAG retrieval MUST be explicitly scoped to the user's authorized workspace (`Workspace.id` or `user@example.com_workspace`).
   - `RoleAclPostprocessor` MUST filter out restricted assets before returning context to non-admin users.

2. **Data Encryption & Transport Controls**:
   - All cloud database connections (`Polygres Cloud`) MUST use mandatory TLS 1.3 / SSL encryption (`sslmode=require`).
   - Sensitive payload contents stored in database columns MUST utilize AES-256-GCM encryption (`crypto_utils.py`).

3. **Zero Data Retention AI Inference**:
   - External model calls routed through OpenRouter MUST enforce zero-retention enterprise privacy flags. Customer knowledge base content MUST NEVER be logged or used for third-party training.

## 🧠 Persistent Memory Agent Workflows

AI Agents working in Krusch-Nexus MUST follow these standardized persistent memory workflows powered by `krusch-context-mcp`:

1. **Session Start / Context Recall**:
   - At session start or when beginning a complex task, query historical memory using `krusch_context_search_memory` (e.g. `query="OpenRouter failover settings"` or `query="sync provider architecture"`).
   - Use `krusch_context_list_memories` to review recent activity and in-flight decision trajectories.

2. **Capturing Decisions & Bug Solutions**:
   - **Bugs**: When an unexpected error or runtime bug is fixed, write a memory record via `krusch_context_write_state(category="bugs", content="...")`.
   - **Architectural Decisions**: When a core design choice or provider default is established, record it via `krusch_context_write_state(category="decisions", content="...")`.
   - **Lessons & Gotchas**: Record operational constraints via `krusch_context_write_state(category="lessons", content="...")`.

3. **Session Close Memory Persistence**:
   - Before completing a major milestone or closing a session, write a summary via `krusch_context_write_state(category="activity", content="...")` and record open roadmap items under `category="priorities"`.
