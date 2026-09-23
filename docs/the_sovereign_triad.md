# The Sovereign Triad: Unifying Document Ingestion, Statutory Graphs, and Agentic Working Memory

> **Author**: Kevin Ruschman  
> **Date**: September 2026  
> **Status**: Systems Architecture Essay (Companion to *The Ingestion Imperative*)  
> **Scope**: Document Geometry, Authority-Weighted Retrieval, Proposition Grounding, Episodic Working Memory, and Closed-Loop Agent Trajectories

---

## Abstract & Systems Thesis

Autonomous AI agents fail in high-stakes domains—such as legal research, regulatory compliance, financial auditing, and engineering oversight—not because foundation models lack intelligence, but because standard architectures decouple **document ingestion**, **domain authority**, and **cognitive working memory** into three isolated, amnesiac silos.

1. **Ingestion Pipelines** treat complex documents as unstructured string streams, discarding physical page numbers, multi-column layouts, table geometry, and character span offsets.
2. **Domain Retrieval Systems** treat statutes, regulations, and contracts as flat "bags of chunks," ignoring statutory hierarchy, temporal validity, appellate deference, and proposition verification.
3. **Agentic Context Frameworks** treat memory as raw chat history dumps, suffering from catastrophic token bloat, attentional degradation, and multi-session amnesia.

When these three components operate in isolation, agents hallucinate plausible citations, cite preempted municipal codes, and repeat discarded analytical mistakes across sessions.

This essay articulates the architecture of the **Sovereign Triad**—a three-pillar local-first ecosystem engineered across three homelab repositories:
- **`krusch-nexus` (The Ingestion Spine)**: Preserves document geometry, character span coordinates, and layout-true citations via Poppler, Tesseract OCR, and boundary-aware chunking.
- **`krusch-law` (The Sovereign Statutory Engine)**: Enforces versioned statutory graphs, authority-weighted retrieval (1.25x to 0.8x), assertion-level proposition grounding, and confidential matter isolation.
- **`krusch-context-mcp` (The Agentic Operating System)**: Supplies persistent episodic working memory, Lakebase SQLite compute caching, proactive trajectory auditing, and L2 neural semantic routing.

By binding these three systems into a unified, air-gapped feedback loop running entirely on local PostgreSQL 16 and Ollama, we achieve an autonomous, self-auditing intelligence substrate with zero cloud egress.

---

```
                                  ┌──────────────────────────────────────────────────────────┐
                                  │                PHYSICAL INGESTION BOUNDARY               │
                                  │            (PDFs, Scans, DOCX, EML, Markdown)            │
                                  └─────────────────────────────┬────────────────────────────┘
                                                                │
                                                                ▼
                                  ┌──────────────────────────────────────────────────────────┐
                                  │                  KRUSCH-NEXUS (Spine)                    │
                                  │  • Poppler layout-true extraction & Tesseract 5.3.4 OCR  │
                                  │  • Bit-for-bit span offset tracking & page coordinates   │
                                  │  • Boundary-aware structural chunking (Section / Item)   │
                                  │  • Hybrid RRF (k=60): tsvector + pgvector (1024d)        │
                                  └──────────────────────┬───────────────────┬───────────────┘
                                                         │                   │
                                   Public Codes / Exhibit│                   │Confidential Facts
                                                         ▼                   ▼
┌────────────────────────────────────────────────────────┐                   ┌────────────────────────────────────────────────────────┐
│               KRUSCHLAW (Domain Engine)                │                   │            KRUSCH-CONTEXT-MCP (Agent OS)               │
├────────────────────────────────────────────────────────┤                   ├────────────────────────────────────────────────────────┤
│ • Versioned Statutory Graph & Legislative Hierarchy    │                   │ • Persistent Episodic Memory (Priorities/Bugs/Lessons) │
│ • Authority Weighting (Statute > Reg > Ordinance)      │◄─────────────────►│ • Lakebase Compute Cache (.agent/memory.db + Postgres) │
│ • Assertion-Level Proposition Verifier (Grounding Gate)│  Proactive Audits │ • Active Memory Hygiene (Superseding & Invalidation)   │
│ • Air-Gapped Matter Evidence & Hard Purge Isolation    │  & Semantic Route │ • Trajectory Auditor (`proactive_nudge` Interceptor)   │
│ • Ethical Refusal: CANNOT_DRAFT_WITHOUT_AUTHORITIES    │                   │ • L2 Neural Semantic Router (13-Tool Lean Core Profile)│
└────────────────────────────────────────────────────────┘                   └────────────────────────────────────────────────────────┘
                                 │                                                                   │
                                 └─────────────────────────────────┬─────────────────────────────────┘
                                                                   ▼
                                  ┌──────────────────────────────────────────────────────────┐
                                  │              SHARED SOVEREIGN SUBSTRATE                  │
                                  │  • PostgreSQL 16 + pgvector (HNSW Cosine Distance)       │
                                  │  • Local Ollama Daemon (BAAI bge-large-en-v1.5, 1024d)   │
                                  │  • Air-Gapped Loopback Containment (127.0.0.1)           │
                                  └──────────────────────────────────────────────────────────┘
```

---

## 1. The Tripartite Anatomy of Agent Failure

To understand why autonomous legal and compliance agents fail, one must examine the chain of transmission through which a real-world document is converted into an agent action:

### Failure Mode 1: The Severed Citation Chain (Ingestion Failure)
Standard document splitters take a 40-page commercial lease or municipal code PDF, strip out whitespace and headers, and split text into fixed 500-token chunks with 50-token overlap.
* **The Consequence**: A critical clause in Section 8.22.030 on Page 14 is severed from its section title on Page 13. The retrieved chunk contains orphan text: *"The tenant may petition the board within sixty days..."* 
* When the downstream model cites this chunk, it has no page number, no parent section identifier, and no document hash. The model hallucinates a plausible-sounding citation: *"Under California Civil Code § 1942..."* The citation chain is dead on arrival.

### Failure Mode 2: Authority Blindness (Retrieval Failure)
Semantic vector search calculates cosine similarity between the query embedding and chunk embeddings. But in statutory and regulatory reasoning, **semantic similarity does not equal governing authority**:
* A repealed 1998 city ordinance on security deposit interest may have a **0.89 cosine similarity** to a tenant grievance query.
* The controlling 2024 California Civil Code § 1950.5 amendment capping deposits at one month's rent may have a **0.78 cosine similarity**.
* A naive vector search ranks the obsolete municipal ordinance above the controlling state statute. The agent drafts a legal demand letter based on repealed law.

### Failure Mode 3: Session Amnesia & Trajectory Drift (Cognitive Failure)
Legal and compliance workflows are multi-turn, multi-session, and iterative:
* In Session 1, an attorney spends 45 minutes clarifying that the subject property is located in an unincorporated county island exempt from municipal rent caps, and notes that the presiding judge in Department 51 strictly enforces 3-day notice service rules.
* In Session 2, the user opens a new chat window. The agent starts from zero. It immediately re-suggests filing a municipal rent board petition and drafting an answer ignoring the 3-day notice defect.
* The agent cannot maintain state, cannot self-correct, and repeats disproven hypotheses.

The Sovereign Triad was designed specifically to eliminate each of these three failure modes.

---

## 2. Pillar 1: KruschNexus — The Ingestion Spine

KruschNexus serves as the physical document anchor for the ecosystem. Its primary design mandate is: **Never sever a word from its spatial and structural origin.**

### Architectural Invariants of KruschNexus
1. **Layout-True Text Extraction via Poppler**:
   Instead of using simple text extraction streams that mangle multi-column PDF briefs and contractual indemnity schedules, KruschNexus invokes `pdftotext -layout` via Poppler. Column boundaries are preserved, tabular columns do not interleave, and indentation hierarchies remain intact.
2. **Optical Character Recognition Fallback**:
   When pages contain scanned exhibits, handwritten signatures, or municipal stamp seals, KruschNexus routes image pages through Tesseract OCR (v5.3.4), preserving bounding coordinates and tagging the chunk record with `ocr_processed: true`.
3. **Bit-for-Bit Span Offset Tracking**:
   Every parsed chunk does not simply store its text string; it records:
   - `file_sha256`: Cryptographic hash of the source binary.
   - `page_number`: 1-indexed physical PDF page number.
   - `char_start` and `char_end`: Exact character offsets within the source page text stream.
   - `heading_path`: The full breadcrumb stack (e.g., `Article IV > Chapter 8.22 > Section 8.22.360 > Subsection (A)(1)`).
4. **Boundary-Aware Chunking (No Fixed Token Windows)**:
   KruschNexus prohibits arbitrary token chunking. Chunking boundaries must align with natural structural demarcations: section headers, article breaks, lease covenants, and email thread delims. Breadcrumbs are stored in dedicated metadata columns rather than prepended into the text body, preventing embedding vector drift.

### The Fail-Closed Hybrid Retrieval Contract
KruschNexus rejects pure vector search. All queries execute through a PostgreSQL Reciprocal Rank Fusion (RRF) query combining:
- **Dense Vector Search**: `pgvector` HNSW index on 1,024-dimensional normalized Euclidean distances ($1 - (\mathbf{u} \cdot \mathbf{v})$).
- **Sparse Full-Text Search**: GIN index on `to_tsvector('english', content)` computing cover-density ranking (`ts_rank_cd`).
- **Section Code Boosting**: Deterministic boosting (1.3x) for exact alphanumeric matches against statutory sections (`§ 1950.5`, `OMC 8.22.030`).
- **Quoted Phrase Boosting**: String matching boost (1.2x) when exact user phrases appear verbatim in chunk text.

---

## 3. Pillar 2: KruschLaw — The Sovereign Statutory Engine

Where KruschNexus understands document layout, KruschLaw understands **governing legal authority**.

### 1. The Versioned Statutory Graph
Law is not a collection of static essays; it is a versioned, directed acyclic graph. KruschLaw represents municipal codes, state statutes, and administrative regulations within its `laws_vectors` schema:

```sql
CREATE TABLE laws_vectors (
    id SERIAL PRIMARY KEY,
    jurisdiction VARCHAR(100) NOT NULL,    -- "California Civil Code", "Oakland Municipal Code"
    state VARCHAR(2) DEFAULT 'CA',
    city VARCHAR(100),
    title VARCHAR(255) NOT NULL,
    section VARCHAR(100) NOT NULL,          -- "Section 8.22.360", "§ 1950.5"
    parent_section VARCHAR(100),            -- Enables recursive parent hydration
    authority_class VARCHAR(50) NOT NULL,   -- "controlling_statute", "municipal_ordinance", etc.
    definitions_ref VARCHAR(255),           -- Link to section-level definition dictionary
    exceptions_ref VARCHAR(255),            -- Link to statutory exception provisions
    repealed BOOLEAN DEFAULT FALSE,
    preempted_by VARCHAR(100),              -- e.g. "Preempted by Cal. Civ. Code § 1947.12"
    effective_date DATE,
    content TEXT NOT NULL,
    embedding vector(1024)
);
```

### 2. Authority-Weighted Scoring
When an inquiry touches multiple jurisdictional layers, KruschLaw multiplies the hybrid RRF score by an authority coefficient:
$$\text{FinalScore} = \text{Score}_{\text{RRF}} \times \mathbf{W}_{\text{authority}}$$

| Authority Tier | Weight | Rationale |
|---|---|---|
| **Controlling Statute** | **1.25x** | State legislative enactments (e.g., California Civil Code) that preempt local rules. |
| **Implementing Regulation** | **1.15x** | Formal agency administrative codes (e.g., CCR, RAP Board Regulations). |
| **Municipal Ordinance** | **1.00x** | City/County codes (e.g., Oakland Municipal Code, SF Administrative Code). |
| **Secondary Commentary** | **0.80x** | Practice guides, legal aid manuals, and internal case summaries. |

Furthermore, whenever a section citing definitions or statutory exceptions is retrieved, KruschLaw automatically hydrates the referenced sections from the graph, guaranteeing that an agent reading a prohibition also sees its statutory exceptions.

### 3. Assertion-Level Proposition Grounding
Rather than trusting the language model to generate accurate text from retrieved context, KruschLaw implements an automated **Assertion-Level Proposition Grounding Scanner**:
1. Generated draft briefs are parsed into discrete factual and legal propositions:
   $$\text{Draft} \longrightarrow \{p_1, p_2, \dots, p_n\}$$
2. Each proposition $p_i$ is cross-referenced against the verbatim spans of the retrieved statutory authorities.
3. Each assertion is classified into one of four deterministic states:
   - ❌ **Invented Citation**: The cited statutory code or section does not exist in the database.
   - ⚠️ **Wrong Proposition**: The statute exists, but the proposition asserts a condition contrary to the statutory text (semantic divergence).
   - 🛑 **Stale Law**: The cited section has `repealed = true` or contains an active `preempted_by` pointer.
   - ✅ **Supported**: Verbatim quote or high-overlap semantic alignment with extracted source span coordinates.

### 4. Ethical Guardrails & Refusal to Draft
If a user asks KruschLaw to draft a brief for an issue where no supporting authorities exist in the air-gapped corpus, the engine does not sample generic hallucinated law. It triggers an ethical refusal:
$$\text{Status} = \texttt{CANNOT\_DRAFT\_WITHOUT\_AUTHORITIES}$$
All generated outputs are branded with mandatory UPL (Unauthorized Practice of Law) disclaimers, flagged with `review_required: true`, and categorized as `provisional_work_product: true`.

---

## 4. Pillar 3: KruschContext MCP — The Cognitive Operating System

Even with flawless document parsing (KruschNexus) and rigorous statutory hierarchy (KruschLaw), an autonomous agent will fail if it cannot remember facts across turns, manage token budgets, or detect when its actions drift from known constraints.

KruschContext MCP is the **meta-cognitive context engine** that governs the agent's working memory and tool trajectory.

### 1. The Lakebase Architecture (Compute / Storage Decoupling)
Standard agent memory systems either store everything in remote cloud databases (high latency) or write unstructured JSON files to disk. KruschContext implements a two-tier **Lakebase Architecture**:
- **Local Compute Cache**: Each project workspace maintains a zero-latency SQLite database (`.agent/memory.db`). Reads (nudge lookups, active priorities, project constraints) execute in sub-millisecond local time.
- **Durable Fleet Storage**: Persistent PostgreSQL tables (`ide_agent_memory`, `ide_agent_nuggets`) on the homelab host maintain fleet-wide durability.
- **Sync Fabric**: Asynchronous write-behind pushes local changes to PostgreSQL; read-ahead pulls hydrate the local cache upon initial project initialization.

### 2. Active Memory Hygiene (Superseding & Invalidation)
Traditional vector databases suffer from "memory pollution": once a fact is embedded, it remains forever. If a tenant moves out, or if a settlement agreement is executed, the old fact competes with the new fact during retrieval.

KruschContext introduces first-class **Temporal Lineage**:
- **Explicit Superseding (`supersede_memory`)**: When an operational rule or case fact changes, the new memory points to `supersedes_id`. The old memory is marked `SUPERSEDED` and dropped from standard search indices, while retaining cryptographic audit provenance.
- **Active Invalidation (`invalidate_memory`)**: If a legal theory is rejected by counsel, or a statute is repealed, the agent marks the record as `INVALIDATED` with an explicit reason string. Invalidated records are permanently excluded from agent prompts.

### 3. Steering Nuggets: Zero-Bloat Convention Enforcement
Prompting models with lengthy instruction manuals consumes thousands of tokens per turn and causes attentional drift. KruschContext introduces **Holographic Steering Nuggets**: micro-key-value rules classified as `project`, `user`, or `agent`.
* Example: `key: "alameda_unlawful_detainer_deadline"`, `value: "Answers must be filed within 5 court days of personal service; do not use calendar days."`
* During retrieval, nuggets are matched via semantic similarity and injected as compact 2-line steering constraints, enforcing compliance without prompt bloat.

### 4. L2 Neural Semantic Routing (The 13-Tool Sovereign Core)
Exposing 60+ tools to an LLM degrades reasoning quality and wastes ~3,500 tokens per turn. KruschContext defaults to a lean **13-Tool Core Profile (~900 prompt tokens)**.

To access specialized domains like KruschLaw, KruschContext utilizes an **L2 Neural Semantic Router (`krusch_context_semantic_route`)**:
- Calibrated archetypes and exemplars are indexed in PostgreSQL using `bge-large` centroids.
- When an agent or user presents an unstructured prompt (*"Does the Oakland rent board allow owner move-in evictions if the landlord owns multiple properties?"*), the semantic router computes cosine distance against the archetype centroids.
- The router detects the `legal_statutory_research` centroid ($> 0.65$ confidence) and dynamically exposes KruschLaw's companion extension (`krusch_law_search_ordinances`, `krusch_law_get_section`, `krusch_law_draft_brief`). General coding turns remain completely unburdened by legal tool definitions.

---

## 5. The Unified Trajectory: How the Triad Operates in Concert

When all three systems are connected, the agent trajectory transforms from an error-prone heuristic sequence into an **audited, closed-loop state machine**.

### Sequence Diagram: The Autonomous Legal Defense Loop

```
User / Counsel           KruschContext MCP              KruschLaw                KruschNexus
      │                         │                           │                         │
      │ 1. Upload Lease & Notice│                           │                         │
      ├──────────────────────────────────────────────────────────────────────────────►│ (Poppler / OCR)
      │                         │                           │                         │ Extracts layout,
      │                         │                           │                         │ page numbers,
      │                         │                           │  2. Index Matter Facts  │ char spans
      │                         │                           │◄────────────────────────┤
      │                         │                           │ (laws_vectors/evidence) │
      │                         │  3. Cache Working State   │                         │
      │                         │◄──────────────────────────┤                         │
      │                         │ (.agent/memory.db)        │                         │
      │                         │                           │                         │
      │ 4. "Analyze Defense"    │                           │                         │
      ├────────────────────────►│                           │                         │
      │                         │ 5. L2 Semantic Route      │                         │
      │                         ├──────────────────────────►│                         │
      │                         │ (legal_counsel archetype) │                         │
      │                         │                           │                         │
      │                         │                           │ 6. Authority Search     │
      │                         │                           │ (Controlling Statutes)  │
      │                         │                           │ OMC § 8.22.360 (1.25x)  │
      │                         │                           │                         │
      │                         │ 7. Draft Brief with Claims│                         │
      │                         │◄──────────────────────────┤                         │
      │                         │                           │                         │
      │                         │ 8. PROACTIVE AUDIT HOOK   │                         │
      │                         │ (proactive_nudge intercept)                         │
      │                         ├──────────────────────────►│                         │
      │                         │ Check Citation Validity   │ 9. Grounding Scanner    │
      │                         │ & Proposition Drift       │ Claims vs Verbatim Spans│
      │                         │                           │ Result: WRONG_PROP (OMI)│
      │                         │ 10. Trajectory Warning    │                         │
      │                         │◄──────────────────────────┤                         │
      │                         │ 🛑 "Notice is 120 days,   │                         │
      │                         │     not 60 days."         │                         │
      │                         │                           │                         │
      │                         │ 11. Self-Correction Turn  │                         │
      │                         │ Re-synthesize with exact  │                         │
      │                         │ statutory span coordinates│                         │
      │                         │                           │                         │
      │ 12. Grounded Legal Brief│                           │                         │
      │◄────────────────────────┤                           │                         │
      │ (Review Required + UPL) │                           │                         │
      │                         │ 13. Persist Matter Lesson │                         │
      │                         ├───────────────────────────┘                         │
      │                         │ (supersede old theories   │                         │
      │                         │  in Lakebase memory)      │                         │
```

### The 5 Steps of the Closed Loop

1. **Geometry-Preserving Ingestion**: KruschNexus ingests the tenant's lease and 30-day notice, recording exact page numbers and character bounding offsets into KruschLaw's `matter_evidence` partition.
2. **Lean Intent Routing**: The user asks for an assessment. KruschContext's L2 router matches the query against the legal archetype centroid and loads the `law` companion tools into the turn context.
3. **Authority-Weighted Retrieval**: KruschLaw retrieves relevant sections of the Oakland Municipal Code, applying a 1.25x multiplier to the controlling just cause ordinance (OMC § 8.22.360) while discounting secondary commentaries.
4. **Proactive Grounding Interception**: As the agent drafts its preliminary analysis, KruschContext's `proactive_nudge` detects statutory citations in the trajectory text stream. It halts generation, queries KruschLaw's `verify_assertion_grounding` endpoint, identifies an erroneous notice duration claim, and injects a real-time warning nudge into the model's scratchpad.
5. **Episodic Persistence**: The agent corrects the brief, cites the exact statutory span, and writes the verified legal conclusion into KruschContext's Lakebase memory (`category: 'lessons'`, `project: 'matter-oakland-104'`). If the local rent board subsequently amends the notice requirements, KruschContext's `supersede_memory` tool updates the knowledge graph, preventing the agent from ever citing the outdated rule in future sessions.

---

## 6. Regulatory Code-to-Statute Traceability

The ultimate payoff of the Sovereign Triad extends beyond legal briefs into **software engineering and regulatory compliance**.

In modern enterprises, software codebases regularly encode statutory requirements:
- Fintech applications encode Regulation E electronic fund transfer dispute windows.
- Proptech platforms encode state security deposit return deadlines.
- Healthcare platforms encode HIPAA audit log retention schedules.

Because KruschContext MCP maintains a structural **AST Symbol Graph** (`git_symbols`, `symbol_edges`, `symbol_graph`) and KruschLaw maintains a **Statutory Graph** (`laws_vectors`), we achieve **Bidirectional Regulatory Traceability**:

```typescript
/**
 * @compliance Cal. Civ. Code § 1950.5(g)(1)
 * @statute_id law_ca_civ_1950_5_g
 * Mandatory 21-calendar-day security deposit accounting and refund.
 */
export function calculateDepositRefund(deposit: number, deductions: DeductionItem[]): RefundResult {
  // Business logic implementing statutory mandate
  ...
}
```

When the California legislature amends Cal. Civ. Code § 1950.5 to reduce deposit return windows or cap deductions, KruschNexus ingests the new chaptered bill. KruschLaw flags the statutory node as amended. 

A continuous integration job queries KruschContext:
```javascript
// Reverse impact analysis: find all code functions implementing this amended statute
const affectedSymbols = await pool.query(`
    SELECT s.symbol_name, s.file_path, s.line_number 
    FROM git_symbols s
    WHERE s.docstring LIKE '%Cal. Civ. Code § 1950.5%'
`);
```

Before a single line of software drifts out of legal compliance, the developer's IDE agent highlights the affected functions, cites the newly enacted statutory span, and drafts the necessary code refactor.

---

## 7. The Homelab Sovereign Matrix

All three pillars of this architecture were built to run on standard homelab workstations with zero cloud dependencies:

| System Layer | Implementation Specification | Hardware Footprint |
| :--- | :--- | :--- |
| **Ingestion Engine** | `krusch-nexus` (FastAPI / Poppler / Tesseract 5.3.4) | Local CPU, ~512MB RAM in library mode |
| **Statutory Graph** | `krusch-law` (FastAPI / pgvector 16 / SQLAlchemy) | Local PostgreSQL 16 container (Port: 5435) |
| **Agent OS / MCP** | `krusch-context-mcp` (Node 22 / Stdio JSON-RPC / SQLite) | Local Node process, ~900 prompt tokens (Port: 5432) |
| **Embedding Space** | Local Ollama (`baai/bge-large-en-v1.5`, 1,024 dims) | Shared across all 3 systems; normalized cosine similarity |
| **Reasoning Model** | Local Ollama (`qwen2.5:14b` or `qwen2.5:7b`) | 12GB - 24GB VRAM workstation or local CPU |
| **Network Boundary** | Strict Loopback (`127.0.0.1`), zero cloud egress | RFC1918 private containment |

---

## Conclusion: Data Sovereignty as Cohesive Systems Engineering

True data sovereignty is not achieved by simply running an open-weight LLM on a desktop. A local model that receives broken text chunks will hallucinate just as readily as a cloud model. A local agent that forgets context across sessions is no more productive than an amnesiac chat window.

Data sovereignty succeeds only when the entire pipeline—from the physical geometry of the source document, to the jurisdictional hierarchy of governing law, to the episodic memory of the agent—is engineered as a unified, self-auditing control system.

By establishing **KruschNexus** as the physical document spine, **KruschLaw** as the authoritative statutory graph, and **KruschContext MCP** as the cognitive working memory harness, we replace probabilistic guessing with verifiable, grounded systems intelligence.
