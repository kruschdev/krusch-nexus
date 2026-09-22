# Krusch-Nexus (Institutional Knowledge) — Specification

> **Author**: kruschDev
> **Date**: 2026-05-07
> **Status**: Draft

---

## 1. What Is This?

Krusch-Nexus is a fully local, open-source alternative to enterprise Institutional Knowledge products. It is designed for privacy-conscious individuals and highly regulated businesses (e.g., legal, healthcare, enterprise operations, engineering) that need advanced RAG and document analysis without sending sensitive operational data to cloud providers. It combines robust document parsing (Docling), hybrid retrieval (PostgreSQL/pgvector + graph DBs), and local LLMs (Ollama) to deliver high accuracy and auto-compounding institutional intelligence in an air-gapped environment.

## 2. User Stories

- As a **knowledge worker**, I want **to chat with project files, spreadsheets, and memos**, so that **I can quickly extract precedents and risks without manual review.**
- As a **business operations manager**, I want **new operational documents to be automatically ingested and cross-referenced**, so that **our institutional knowledge compounds without administrative overhead.**
- As a **security officer**, I want **the entire system to run locally on our hardware**, so that **highly sensitive workspace data never leaves our network.**

## 3. Core Features

| Feature | Priority | Notes |
|---------|----------|-------|
| Local RAG Engine | Must-have | LlamaIndex over PostgreSQL (pgvector) for document querying |
| Document Parsing Pipeline | Must-have | Docling to handle complex PDFs, spreadsheets, and unstructured data |
| Graph Knowledge Base | Must-have | LightRAG/GraphRAG variant for relational queries across workspaces (entities, relationships) |
| Auto-Ingestion Daemon | Must-have | Cron/pipeline to monitor directories and automatically embed new workspace files |
| Agentic Workflow Pipeline | Must-have | LangGraph to chain reasoning (screen → analysis → report) |
| Local LLM Integration | Must-have | Ollama integration (e.g., Qwen/Llama3) for entirely air-gapped reasoning |
| Three-Layer Audit Trail | Must-have | LangGraph exposes intermediate reasoning (Claim → Logic → Source) |
| Auto-Categorization | Must-have | Local LLM extracts metadata (Industry, Risk) during ingestion |
| Risk/Precedent Workflows | Must-have | Specific LangGraph entry points for common operational queries |
| Native Structured Export | Nice-to-have | Streamlit export of metric extractions to Excel/CSV |
| Point-of-Work API | Nice-to-have | Read-only REST endpoint for homelab integration |
| Temporal Decay | Must-have | Time-based decay function prioritizing recent knowledge |
| Cascade Router | Must-have | Dynamic model escalation based on query complexity |
| Agentic Proxy | Must-have | Intelligent routing between RAG, GraphRAG, and SQL layers |
| Text-to-SQL SLM | Must-have | Dedicated local SQL SLM for deterministic DB queries |

## 4. Technical Constraints

- **Stack**: Python / FastAPI / `@krusch/toolkit` Python equivalents
- **Frontend**: Streamlit (Chosen for maximum local security, auditability, and zero external telemetry)
- **Database**: PostgreSQL (pgvector for embeddings, relational tables for Graph relationships). Chosen to minimize attack surface by avoiding additional DB containers (like Neo4j).
- **Auth**: Simple local JWT / RBAC for internal deployment
- **AI/LLM**: Local-first via Ollama (No frontier model fallback to ensure strict air-gap)
- **Dependencies**: LlamaIndex, LangGraph, local embedding models

## 5. Data Model

```text
Workspace → has many → Documents (Specs, Memos, Models)
Document → parsed into → Chunks (Vector Embeddings in pgvector)
Document → parsed into → Entities/Relationships (Graph Nodes/Edges)
UserQuery → generates → RAG Context → generates → Response
```

## 6. UI/UX

- **Chat Interface**: Standard conversational UI for querying the knowledge base.
- **Workspace Dashboard**: Overview of uploaded workspaces, extraction status, and key entities.
- **Source Citations**: Crucial for accuracy—every claim must link back to the exact page/table in the source document.

## 7. Edge Cases & Gotchas

- [ ] **Data Model Fidelity**: Open-source struggles with live data models. For maximum privacy, we will rely strictly on local Python libraries (pandas/openpyxl) to extract raw data, entirely avoiding third-party parsing APIs.
- [ ] **Hallucinations**: Business and operational accuracy is paramount. We must tune the RAG pipeline (chunking strategy, PageIndex) to avoid hallucinating facts and numbers.
- [ ] **Hardware Constraints**: Running local models + graph RAG is compute-intensive. Deployments will require dedicated local GPU hardware.

## 8. Acceptance Criteria

- [ ] System can ingest a complex PDF or spreadsheet and accurately answer questions about specific tables.
- [ ] System runs entirely without internet access (once models are downloaded).
- [ ] New documents dropped into a specific folder are automatically embedded within 5 minutes.
- [ ] Answers include citations to the source document chunks.

## 9. Out of Scope

- Native, interactive Excel plugin (for v1)
- Advanced multi-tenant SaaS billing
- Real-time stock price / external web browsing (to maintain air-gap)

## 10. Delivery Phases

| Phase | Scope | Acceptance |
|-------|-------|------------|
| 1 | Core Local RAG | Can ingest PDFs into pgvector and query them accurately via LlamaIndex + Ollama. |
| 2 | Graph Relations | Integrate GraphRAG to answer cross-workspace questions (e.g., "Compare entities in Workspace A vs Workspace B"). |
| 3 | Agentic Analysis & Workflows | LangGraph pipelines for multi-step reasoning, Three-Layer Audit Trail, Auto-Categorization, Native Structured Exports, Point-of-Work API, Temporal Decay weights, Cascade Router, Agentic Proxy, and Text-to-SQL SLM. |
