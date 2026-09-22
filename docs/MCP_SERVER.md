# Krusch-Nexus Cloud-Native Business RAG & MCP Server Guide

The **Krusch-Nexus MCP Server** serves as the central Model Context Protocol (MCP) gateway for institutional knowledge, company SOPs, financial reports, client histories, and business protocols.

It incorporates **`krusch-context-mcp`** as its foundational retrieval engine for vector search (`bge-large` 1024d), reranking, episodic memory, and GraphRAG entity linking. Whenever `krusch-context-mcp` receives capability updates (ACM token lifecycle, memory consolidation, Rubric4Setwise reranking), **Krusch-Nexus automatically inherits all updated context features**.

---

## ☁️ Cloud-Native Architecture & Privacy Standards

- **Cloud-Native Provider Defaults**: OpenRouter Cloud AI (`qwen/qwen-2.5-coder-32b-instruct` & `baai/bge-large-en-v1.5`) paired with Polygres Cloud `pgvector` storage.
- **End-to-End Encryption**: Mandatory TLS 1.3 / SSL (`sslmode=require`) for all Polygres cloud PostgreSQL database connections.
- **Zero Data Retention**: AI inference requests sent to OpenRouter operate under strict zero-retention enterprise privacy policies.
- **Role-Based Access Control (RBAC & ACLs)**: Enterprise document access isolation (`RoleAclPostprocessor`) ensures strict tenant isolation across workspaces.

---

## 🔌 Comprehensive MCP Tool Catalog

### 🏢 Business RAG & Knowledge Tools

1. **`nexus_query_business_knowledge`**
   - **Description**: Query company SOPs, financial reports, emails, policies, and client memos using hybrid 1024d vector search + GraphRAG relational entity linking.
   - **Parameters**: `query` (str), `workspace_name_or_id` (optional str), `include_graph_context` (bool).

2. **`nexus_get_email_draft_context`**
   - **Description**: Formats company business guidelines, SOP rules, and client context to enable an AI agent to draft professional, policy-compliant email replies.
   - **Parameters**: `email_subject_or_content` (str), `client_or_topic` (optional str), `workspace_name_or_id` (optional str).

3. **`nexus_ingest_business_document`**
   - **Description**: Ingest a new document (SOP, email thread, financial report, memo) into the knowledge base directly via MCP tool call.
   - **Parameters**: `filename` (str), `content` (str), `workspace_name` (str), `category` (str).

4. **`nexus_get_sop_checklist`**
   - **Description**: Extract structured phase-by-phase action items and operational checklists for any business procedure.
   - **Parameters**: `procedure_query` (str), `workspace_name_or_id` (optional str).

5. **`nexus_find_expert`**
   - **Description**: Identify internal team subject-matter experts (SMEs), role owners, or document authors for any topic or account.
   - **Parameters**: `topic_or_query` (str).

6. **`nexus_sync_google_workspace`**
   - **Description**: 1-click synchronization of Gmail threads, Google Drive folders, Docs, Sheets, and Slides.
   - **Parameters**: `workspace_id` (int), `sync_type` (str).

7. **`nexus_list_workspaces` & `nexus_list_documents`**
   - **Description**: Explore available knowledge spaces and ingested document catalogs.

---

### 🧠 Episodic Memory & Foundational Engine Tools (from `krusch-context-mcp`)

- **`krusch_context_search_memory`**: Semantic search over historical lessons, bugs, decisions, and activity.
- **`krusch_context_write_state`**: Save a new episodic memory record (`priorities`, `outcomes`, `lessons`, `bugs`, `activity`).
- **`krusch_context_list_memories`**: Retrieve recent memories chronologically.
- **`krusch_context_search_code`**: Perform vector + cross-encoder reranked code search across indexed repositories.

---

## 💾 Persistent Memory Workflow Protocol for AI Agents

When interacting with Krusch-Nexus, AI Agents MUST execute the following three-phase memory workflow:

1. **Recall Phase (Startup)**:
   - Execute `krusch_context_search_memory(query="...")` or `krusch_context_list_memories()` at the start of a session or task to retrieve past decisions, resolved bugs, and architectural rules.

2. **State Capture Phase (Execution)**:
   - When a bug is fixed, invoke `krusch_context_write_state(category="bugs", content="...")`.
   - When a design decision is made, invoke `krusch_context_write_state(category="decisions", content="...")`.
   - When operational constraints are discovered, invoke `krusch_context_write_state(category="lessons", content="...")`.

3. **Consolidation Phase (Shutdown/Close)**:
   - Summarize work milestones under `category="activity"` and log active roadmap items under `category="priorities"`.
   - Records are vector-indexed (`baai/bge-large-en-v1.5` 1024d) and stored in PostgreSQL for instant cross-session recall.

---

### 💼 Pocket Lawyer Small Business Tools

- **`krusch_business_review_contract`**: Analyze agreements for high/medium liability risks.
- **`krusch_business_assess_risk`**: Evaluate overall legal risk exposure for a business profile.
- **`krusch_business_demand_letter`**: Generate formal debt collection demand letters.
- **`krusch_business_compliance_calendar`**: Calculate Statement of Information filing windows.

---

## Configuration & Client Integration

### 1. Claude Desktop Setup (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "krusch-nexus": {
      "command": "/bin/bash",
      "args": ["/home/krusch/homelab/projects/krusch-nexus/scripts/run_mcp.sh"],
      "env": {
        "NEXUS_API_KEY": "YOUR_NEXUS_API_KEY_HERE",
        "TAGGING_PROVIDER": "openrouter",
        "EMBEDDING_PROVIDER": "openrouter"
      }
    }
  }
}
```

### 2. Cursor / Antigravity Setup (`.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "krusch-nexus": {
      "command": "python3",
      "args": ["-m", "src.backend.mcp_server"],
      "cwd": "/home/krusch/homelab/projects/krusch-nexus",
      "env": {
        "NEXUS_API_KEY": "YOUR_NEXUS_API_KEY_HERE"
      }
    }
  }
}
```

### 3. Remote Network & Mobile Setup (SSE Mode over HTTP)

For remote users, mobile apps, web clients, OpenClaw, or browser extensions connecting over the network:

```json
{
  "mcpServers": {
    "krusch-nexus-sse": {
      "url": "http://10.0.0.85:8002/sse",
      "transport": "sse"
    }
  }
}
```
