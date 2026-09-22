# Krusch Local Agents — Specification

> **Author**: kruschdev
> **Date**: 2026-05-14
> **Status**: Draft

---

## 1. What Is This?

A dual-agent system running entirely on local hardware, powered by DBOS durable execution and grounded by the 444K-file kruschdb knowledge base. At the center is a **Dual-Engine Personal Agent** architecture. It uses a conversational Persona model (`gemma4:e4b`) on kruschserv's 2080 Ti for high-bandwidth user interaction, which delegates complex tool calls to an Executor model (`hermes3:8b`) running on kruschdev (dual 3060s). This abstracts away tool-hallucination quirks while keeping inference fast and grounded. Behind it, a **Coding/Homelab Agent** on kruschdev handles autonomous background work — processing DBOS job queues, code audits, and infrastructure tasks. Both agents share kruschdb as the DBOS substrate, eliminating external API dependencies for day-to-day operations.

## 2. User Stories

- As a developer, I want to ask "how does auth work in berean?" and get a grounded answer with actual file references, so that I don't have to grep the codebase manually.
- As a homelab operator, I want the coding agent to diagnose service failures using fleet memory and past incident reports, so that I get faster resolution.
- As a developer, I want new files ingested via pg-git to be automatically summarized (14b) and embedded (bge-large), so that the knowledge base stays current with zero manual intervention.
- As a developer, I want to submit complex tasks (refactors, audits) to a DBOS queue and have the coding agent process them autonomously, so that work continues while I'm away.
- As a homelab operator, I want the retrieval agent to be always-on as a persistent service, so that any agent or MCP client can query the knowledge base instantly.

## 3. Core Features

| Feature | Priority | Notes |
|---------|----------|-------|
| **Personal Agent Harness** | Must-have | Dual-Engine custom agentic loop — Persona (`gemma`) + Executor (`hermes3`) |
| Harness — MCP tool routing | Must-have | Route Executor tool calls to krusch-context, krusch-infra, nuggets, sentinel, etc. |
| Harness — Fleet Telemetry | Must-have | Sentinel MCP enables autonomous system, disk, and docker health checks across nodes |
| Harness — Conversation persistence | Must-have | Store/resume conversations in kruschdb via Persona |
| Harness — Context window management | Must-have | Auto-summarize/prune to stay within 8K context |
| Harness — Multi-channel input | Nice-to-have | Web chat, terminal CLI, API endpoint, Google Chat webhook |
| Retrieval stack — bge-large HNSW search | Must-have | Embed query → cosine search → return top N |
| Retrieval stack — bge-reranker-v2-m3 | Must-have | Rescore top 50 → return top 5 |
| Coding Agent — DBOS job queue consumer | Must-have | Poll `agent_execution_queue`, execute tasks |
| Coding Agent — tool-calling loop with Ollama | Must-have | Multi-turn agentic loop (proven pattern from openclaw-worker) |
| Coding Agent — retrieval tool integration | Must-have | Calls retrieval MCP on kruschserv for grounded context |
| Coding Agent — file read/write/shell tools | Must-have | Same tool surface as openclaw-worker |
| DBOS durable execution | Must-have | Crash recovery, exactly-once, job status tracking |
| Auto-ingest pipeline (summarize + embed) | Must-have | New blobs get 14b summary + bge-large embedding at sync time |
| Nexus dashboard integration | Nice-to-have | Monitor agent status/conversations/jobs in Streamlit UI |

## 4. Technical Constraints

- **Stack**: Node.js (ESM) for DBOS workers + Python for retrieval inference
- **Database**: PostgreSQL (kruschdb on kruschserv:5434) — shared DBOS substrate
- **AI/LLM**: 
  - Personal Agent Persona: **Qwen 3.5 (`qwen3.5:9b`)** on kruschserv (2080 Ti) — fast, conversational response
  - Personal Agent Executor: **DeepSeek R1 (`deepseek-r1:14b`)** on kruschdev (Dual 3060s) — robust reasoning and tool execution
  - Retrieval: **bge-large** (embeddings), bge-reranker-v2-m3 (reranking) — co-located with DB on kruschserv
  - Coding/Flywheel: **Qwen 2.5 Coder (`qwen2.5-coder:14b`)** on kruschdev (Dual 3060s) — async execution and DBOS worker
  - Summaries/Tags: **Qwen 2.5 Coder (`qwen2.5-coder:14b`)** on kruschdev (Dual 3060s) — inline at ingest time
- **Hosting**: 
  - **kruschdev**: Docker containers for Krusch-Nexus (frontend, backend, personal agent, ingest-worker, embed-worker, backfill-worker)
  - **kruschgame**: Systemd service for OpenClaw Swarm Researcher (`openclaw-swarm.service`)
  - **kruschserv**: PostgreSQL (`kruschdb`) + native Ollama inference (Hermes, Gemma, bge-large)
- **Dependencies**: `@krusch/toolkit`, DBOS SDK, Ollama API, sentence-transformers (reranker)
- **GPU Allocation** (2-tier Compute-Data Split):
  - kruschserv 2080 Ti (11GB, ~616 GB/s): Persona (`qwen3.5:9b`) + Embeddings (`bge-large`). Usage: ~7.3GB / 11GB.
  - kruschdev Dual 3060s (24GB, ~360 GB/s): Executor (`deepseek-r1:14b`) + Coding (`qwen2.5-coder:14b`). Usage: ~19GB / 24GB.
  - **Burst Mode**: Both 3060s can be pooled (24GB combined) via Ollama tensor parallelism to run a single large model (32b or 70b q4) on demand — pause autonomous workers, load the big model, run the task, resume normal ops

## 5. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        kruschdb (PostgreSQL)                │
│                                                             │
│  blobs (444K files, embeddings, summaries)                  │
│  ide_agent_memory (319 episodic memories)                   │
│  homelab_memory_v2 (69 v2 brain states)                     │
│  agent_execution_queue (DBOS job queue)                     │
│  HNSW indexes on all vector columns                         │
└──────────┬──────────────────────────────┬───────────────────┘
           │                              │
    ┌──────▼──────────────┐      ┌────────▼─────────────────┐
    │  PERSONAL AGENT     │      │  CODING/HOMELAB AGENT    │
    │  kruschserv         │      │  kruschdev               │
    │  2080 Ti (11GB)     │      │  2× RTX 3060 (24GB)     │
    │  ~616 GB/s • ~7.3GB │      │  ~360 GB/s • ~19GB       │
    │                     │      │                          │
    │  ┌────────────────┐ │ MCP  │  ┌──────────────────┐   │
    │  │ Persona        │ │◄────►│  │ Executor/Critic  │   │
    │  │ qwen3.5:9b     │ │      │  │ deepseek-r1:14b  │   │
    │  │ Chat history   │ │      │  │ Executes Tools   │   │
    │  └────────────────┘ │      │  └──────────────────┘   │
    │  ┌────────────────┐ │      │  ┌──────────────────┐   │
    │  │ bge-large      │ │      │  │ Auto Worker      │   │
    │  │ (embed queries)│ │      │  │ qwen2.5-coder:14b│   │
    │  └────────────────┘ │      │  │ Audits / Jobs    │   │
    │  ┌────────────────┐ │      │  └──────────────────┘   │
    │  │ bge-reranker   │ │      │                          │
    │  │ v2-m3 (rescore)│ │      │  Cascade Escalation:    │
    │  └────────────────┘ │      │  14b → Gemini Flash     │
    │         ↕           │      │                          │
    │    kruschdb ◄───────┤      │                          │
    └─────────────────────┘      └──────────────────────────┘
```

### Data Flow: User Conversation (Personal Agent)

```
1. User asks: "Check container health and tell me if anything is down."
2. Persona Agent (Gemma on 2080 Ti):
   a. Understands request, formulates "delegate_to_executor" tool call.
3. Executor Agent (Hermes on 3060):
   a. Receives instruction.
   b. Calls MCP "get_container_health" on host.
   c. Returns raw JSON telemetry to Persona.
4. Persona Agent:
   a. Synthesizes JSON into a conversational, easy-to-read response.
   b. Optionally calls "delegate_to_executor" again to restart containers if needed.
```

### Data Flow: Autonomous Task (Coding Agent)

```
1. Coding Agent (7b on GPU0) claims task from DBOS queue
2. Model decides it needs context → calls retrieval MCP on kruschserv
3. Retrieval returns 5 reranked results
4. 7b reasons over grounded results → generates code/plan/fix
5. If 7b confidence is low → cascade to 14b (GPU1)
6. If 14b still struggles → cascade to Gemini Flash via krusch-cascade-router
7. Reports result back to DBOS queue
```

### Data Flow: Ingest Pipeline (Self-Maintaining)

```
1. pg-git sync_to_pg.js runs on /close or /commit
2. New/changed files inserted into kruschdb.blobs
3. For each new blob:
   a. qwen2.5-coder:14b generates 1-line summary (Ollama on kruschserv)
   b. bge-large generates 1024d embedding from summary
   c. Both stored in blobs table
4. Knowledge base stays current — no batch backfills needed
```

## 6. Data Model

Existing tables (no schema changes needed):

```
blobs (444K rows)
  ├── id (SHA hash)
  ├── content (bytea)
  ├── summary (text) ← Gemini backfill now, 14b going forward
  ├── embedding (vector(1024)) ← bge-large
  ├── file_name, file_path, repository_id
  └── HNSW index on embedding

ide_agent_memory (319 rows)
  ├── id (uuid)
  ├── category (priorities|bugs|outcomes|lessons|activity)
  ├── content (text)
  ├── embedding (vector(1024))
  └── HNSW index on embedding

agent_execution_queue (DBOS jobs)
  ├── job_id, job_type, status, payload, result
  ├── started_at_ms, picked_at_ms
  └── FOR UPDATE SKIP LOCKED (concurrent-safe polling)
```

## 7. Edge Cases & Gotchas

- [ ] **GPU memory contention**: 14b + bge-large + reranker must coexist on 2080 Ti. Use `OLLAMA_KEEP_ALIVE=5m` to unload idle models.
- [ ] **Reranker cold start**: First rerank call loads ~560MB model. Subsequent calls are fast. Consider pre-warming.
- [ ] **Query too long for bge-large**: 512 token limit. Truncate queries > 400 chars before embedding.
- [ ] **DBOS system DB**: Needs its own database (`krusch_agents_dbos_sys`) separate from kruschdb app tables.
- [ ] **Network partitions**: Coding agent on kruschdev must reach kruschserv:5434 (DB) and kruschserv retrieval MCP. Use Tailscale for reliability.
- [ ] **14b summary quality**: May produce lower quality than Gemini for complex files. Acceptable tradeoff for zero-cost, zero-latency inline processing.
- [ ] **Concurrent DBOS workers**: Multiple coding agents (e.g., kruschgame joining the fleet) must use `FOR UPDATE SKIP LOCKED` to prevent double-claiming jobs.

## 8. Acceptance Criteria

- [ ] Retrieval Agent starts on kruschserv and responds to MCP `search_code` queries in < 500ms
- [ ] Reranker improves search precision (manual eval: top-5 results are more relevant than raw cosine)
- [ ] Coding Agent polls DBOS queue and executes jobs with tool-calling loop
- [ ] Coding Agent can call retrieval MCP and use results in its reasoning
- [ ] New files synced via pg-git are automatically summarized + embedded (verified by checking blobs table)
- [ ] Both agents recover gracefully from crashes (DBOS durable execution)
- [ ] System runs 24/7 with zero API costs

## 9. Out of Scope

- Not building a web chat UI (use Nexus Streamlit dashboard for monitoring)
- Not replacing the IDE agent (Claude/Gemini) — this is for autonomous background work
- Not supporting external LLM providers (fully local by design)
- Will add kruschgame as a third compute node in a future phase
- Will add multi-agent debate (Ideator/Critic/Synthesizer) in a future phase (can port from openclaw-worker)

## 10. Delivery Phases

| Phase | Scope | Acceptance |
|-------|-------|------------|
| 1 — Retrieval MCP | bge-large search + reranker on kruschserv, MCP interface, inline summarize-on-ingest | Can query 444K blobs via MCP and get reranked results |
| 2 — Coding Agent | DBOS worker on kruschdev, tool-calling loop, retrieval integration | End-to-end: submit job → agent searches → generates code → reports result |
| 3 — Self-Maintaining | Auto-ingest pipeline, Nexus dashboard monitoring, fleet scaling | System runs autonomously with zero manual intervention |

## 11. Federated Company Brain & Supervisor Retrieval (Life/Work Integration)

To solve the busywork dilemma and let humans focus on cognitive creation, the Krusch Nexus architecture integrates a federated edge-node database topology:

### 1. SQLite Edge Nodes (Personal Workspace Isolation)
* **Design:** Each employee (or personal department, such as Tiers 1-4 Personal Priorities vs. Tier 5 Work) maintains an isolated local SQLite file (e.g., `personal_priorities.db`, `work_projects.db`).
* **Purpose:** Acts as a localized caching sensor that ingests raw, high-volume inputs (emails, task descriptions, banking histories) offline without cloud exposure or global leakage.

### 2. Central PostgreSQL Brain (`kruschdb` / `nexus_db`)
* **Design:** Local agents generate embeddings (`bge-large`) and high-level abstract summaries from their SQLite caches, syncing only this metadata to the central PostgreSQL repository.
* **Purpose:** Serves as the global company index for cross-system searches and priority audits, keeping the memory footprint low and securing raw data.

### 3. Federated Supervisor Querying
* **Strict Roster Filters:** If a non-supervisor queries the central database, the PG query automatically scopes the vector search by `owner_id = self.id`, guaranteeing zero visibility into other workspaces.
* **Authorized Raw Retrieval:** If a user with `role = 'supervisor'` queries the central index, the PG database retrieves global vector matches. The coordinator then performs a federated query directly to the respective SQLite databases to pull down the raw, unsummarized files for audit.
* **Busywork Elimination:** Routines, bills, client tracking, and formatting are offloaded to background workers, freeing human brains to design, manifest, and create value.

### 4. Human-in-the-Loop (HITL) Approval Gate
* **No Direct Execution:** Any action proposed by the LLM agent (e.g., sending an email reply to a client, drafting a calendar invite, planning a bill payment transaction, or updating a database record) is strictly staged in a local `pending_actions` queue.
* **Triage Interface:** The agent presents these proposed actions to you on your daily agenda (`nexus get-agenda`).
* **Enforced Gated Decision:** Actions remain in a pending state until you explicitly issue an approval command:
  * `nexus approve <action_id>` -> Triggers the actual system execution tool.
  * `nexus edit <action_id> --feedback "change this..."` -> Requests the LLM to modify and restage.
  * `nexus reject <action_id>` -> Discards the action.
* **Goal:** This prevents any auto-pilot hallucinations from taking real-world effects, ensuring you maintain absolute control over the outputs of the LLM before they go live.
