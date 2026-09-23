# Citation Spine, Statutory Graph & Working Memory: Systems Architecture Note on Bounding Generative Error in Legal Workflows

> **Author**: Kevin Ruschman  
> **Date**: September 2026  
> **Status**: Systems Architecture Note (Companion to *The Ingestion Imperative*)  
> **Scope**: Ingestion Geometry, Statutory Deference, Claim-Level Entailment, and Episodic Working Memory

---

## Abstract & Scope

Generalist retrieval-augmented generation (RAG) fails in high-stakes legal workflows because it treats law as text rather than a versioned jurisdictional hierarchy, and treats memory as conversational history rather than structured state.

When evaluated against synthetic bootstrap fixtures, local pipelines appear solved (100% Recall@5, 100% rejection of invented citations). When evaluated against held-out statutes and semantic divergence tests, the real systems boundaries emerge:
- **Held-Out Recall@1 drops to 66.7%**: Lexical and vector similarity struggle when colloquial grievances diverge from formal legislative drafting.
- **Misgrounding Detection stalls at 70.0%**: While catching invented citations (non-existent sections) and stale law (repealed statutes) is straightforward syntax matching, verifying whether a genuine statute actually entails a specific claim is the central difficulty of legal RAG.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       MEASURED RETRIEVAL & GROUNDING BASELINE               │
├──────────────────────────────────────┬──────────────────────────────────────┤
│    FIXTURE CORPUS (Bootstrap)        │    HELD-OUT & EMPIRICAL REALITY      │
├──────────────────────────────────────┼──────────────────────────────────────┤
│ • Recall@1: 92.0%                    │ • Held-Out Recall@1: 66.7% (n=12)    │
│ • Recall@5: 100.0%                   │ • Held-Out Recall@5: 100.0%          │
│ • Invented Citations Blocked: 100.0% │ • Divergent Proposition Acc: 70.0%   │
│ • Stale Law Detected: 100.0%         │ • Priority Inversions: 0             │
└──────────────────────────────────────┴──────────────────────────────────────┘
```

This note documents the design contract of an air-gapped, local-first legal assistance architecture combining three specialized layers:
1. **`krusch-nexus` (Citation Spine)**: Preserves physical page numbers, multi-column geometry, and character span offsets.
2. **`krusch-law` (Statutory Graph & Verifier)**: Models statutory hierarchy, preemption relationships, and assertion grounding.
3. **`krusch-context-mcp` (Working Memory)**: Governs project state, episodic lessons, and tool dispatching.

We do not claim to "replace probabilistic guessing with verifiable intelligence." Rather, **we bound guessing to explicit propositions, enforce mechanical validation before semantic evaluation, and refuse claims when governing authority is absent.**

---

## 1. What Is Implemented vs. What Is Planned

To maintain technical integrity, we explicitly distinguish between active code running in continuous integration and architectural roadmap:

| Component | Status | Implementation Details |
| :--- | :---: | :--- |
| **Span-True Ingestion** | ✅ **Implemented** | Poppler layout extraction, Tesseract OCR fallback, bit-for-bit span offset tracking (`krusch-nexus`). |
| **Hybrid RRF Search** | ✅ **Implemented** | Reciprocal Rank Fusion ($k=60$) combining `pgvector` HNSW cosine distance and `tsvector` cover density (`krusch-nexus`, `krusch-law`). |
| **Grounding Failure Taxonomy** | ✅ **Implemented** | 5-class granular classifier: `entailed`, `contradicted`, `exception_applies`, `insufficient_context`, `not_in_corpus` (`krusch-law`). |
| **Episodic Working Memory** | ✅ **Implemented** | Lakebase architecture: SQLite compute cache (`.agent/memory.db`) synced to PostgreSQL with explicit superseding and invalidation (`krusch-context-mcp`). |
| **Lean Tool Routing** | ✅ **Implemented** | 13-tool core profile (~900 tokens) with dynamic L2 neural centroid dispatching (`krusch-context-mcp`). |
| **Deterministic Jurisdiction Machine** | ✅ **Implemented** | Deprecated scalar multipliers (1.0x). Evaluates temporal status, spatial `applies_if` gates, and preemption graph edges (`krusch-law`). |
| **Two-Pass Claim Verifier** | ✅ **Implemented** | Pass A (mechanical syntax/date) + Pass B (propositional entailment, numbers, duty inversions, statutory exceptions) with inline refusal (`krusch-law`). |
| **Statutory Amendment Pipeline** | ✅ **Implemented** | Computes legislative diffs, transitions nodes to `amended`, and routes affected memories to `STALE_PENDING_REVIEW` queue (`krusch-law`, `krusch-context-mcp`). |
| **Code-to-Statute Traceability Table** | ✅ **Implemented** | Curated `StatuteCodeTraceability` table binding California housing statutes to symbols with attorney review attestations (`krusch-law`). |
| **Unified Runtime API Bridge** | ✅ **Implemented** | Native `/api/laws/search`, `/api/laws/section`, `/api/verify/assertions`, and `/api/cases/brief` endpoints unifying context and law (`krusch-law`). |

---

## 2. Replacing Authority Multipliers with a Jurisdiction Machine

The weakest technical pattern in early legal RAG prototypes is the **authority multiplier** (e.g. boosting state statutes by $1.25\times$ and local ordinances by $1.00\times$). 

**Preemption is not a ranking boost.** A municipal rent control ordinance does not lose 20% of its relevance when a state statute applies; it is either controlling, preempted, or conditionally harmonized under statutory carve-outs.

```
                               ┌───────────────────────────┐
                               │   Candidate Authorities   │
                               │  (Hybrid Vector + Lexical)│
                               └─────────────┬─────────────┘
                                             │
                                             ▼
                               ┌───────────────────────────┐
                               │  Pass 1: Temporal & Place │
                               │  Drop repealed, sunset,   │
                               │  or out-of-jurisdiction   │
                               └─────────────┬─────────────┘
                                             │
                                             ▼
                               ┌───────────────────────────┐
                               │  Pass 2: Preemption Graph │
                               │  Apply explicit edges:    │
                               │  `preempted_by` pointers  │
                               └─────────────┬─────────────┘
                                             │
                                             ▼
                               ┌───────────────────────────┐
                               │  Pass 3: Mandatory Graph  │
                               │  Hydrate linked exception │
                               │  and definition children  │
                               └─────────────┬─────────────┘
                                             │
                                             ▼
                               ┌───────────────────────────┐
                               │   Ranked Live Authorities │
                               │  (Only active law reaches │
                               │   the prompt context)     │
                               └───────────────────────────┘
```

### The Node Specification
Each statutory node in `krusch-law` is modeled with deterministic relationship edges:
- `jurisdiction`: Federal, State, County, City, Agency.
- `instrument_type`: Statute, Regulation, Ordinance, Administrative Ruling.
- `effective_from` / `effective_to`: Date boundaries.
- `status`: `enacted`, `amended`, `repealed`, `sunset`, `enjoined`.
- `relational_edges`: `preempted_by[]`, `preempts[]`, `implements[]`, `defines[]`, `exception_to[]`.
- `applies_if`: Explicit fact gates (e.g. `unincorporated_island = true`, `multi_unit_residential = true`).

### The Two-Stage Retrieval Pipeline
1. **Stage 1 (Retrieval)**: Hybrid RRF retrieves a candidate set of textually and conceptually relevant nodes.
2. **Stage 2 (Jurisdiction Machine)**:
   - Evaluates matter facts against `applies_if` (e.g. property is in an unincorporated county parcel, immediately dropping municipal rent ordinances).
   - Traverses `preempted_by` edges (e.g. statewide Costa-Hawkins Act preempting local vacancy control).
   - Mandatorily attaches definition and exception sub-clauses.
   - Only surviving, legally controlling nodes are supplied to downstream drafting.

---

## 3. Two-Pass Grounding Verification: Mechanical Gate vs. Proposition Entailment

The dominant legal RAG failure mode in production is **misgrounding**: citing an authentic, active statutory section for a legal proposition that the statute does not support. This accounts for our **70.0% divergence baseline**.

Asking the drafting LLM to grade its own output produces self-reinforcing hallucinations. Verification must be decoupled into two distinct passes:

```
Proposed Draft ──► [ PASS A: Mechanical Verification (Target: 100%) ]
                         │
                         ├── Citation exists in corpus?
                         ├── Section alphanumeric string parses?
                         ├── Span offsets resolve to source binary?
                         └── Status was active on matter incident date?
                         │
                         ▼ (Pass A Clear)
                   [ PASS B: Proposition Entailment ]
                         │
                         ├── Decompose draft into atomic claims {c1, c2, ... cn}
                         ├── Bind each claim to specific source span IDs
                         └── Isolated Natural Language Entailment Model:
                               • entailed              ──► Accepted
                               • contradicted          ──► Claim Refused
                               • exception_applies     ──► Flagged for Carve-out
                               • insufficient_context  ──► Claim Refused
                               • not_in_corpus         ──► Claim Refused
```

### Refusing the Claim, Not the Brief
When a draft contains three claims—two supported by Cal. Civ. Code § 1950.5 and one claiming an ungrounded 60-day deposit refund window—the system must **refuse the specific claim**, redacting or striking the unsupported proposition while preserving the valid analysis.

### Defensible Audit Trail
Every verification run records an immutable record in `audit_logs`:
- `claim_text`: The exact sentence generated.
- `source_span_ids`: Primary key pointers into `laws_vectors` or `matter_evidence`.
- `physical_page`: Page coordinates in the underlying PDF.
- `verdict`: `entailed`, `contradicted`, `insufficient_context`.
- `model_version`: Identifier of the verifying model.

---

## 4. Separation of Invariants: Three Distinct Stores

Treating lease exhibits, public statutes, and attorney theories as interchangeable vector records causes severe memory contamination. The architecture enforces three isolated stores with distinct invariants:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          THREE-STORE DATA ISOLATION                         │
├──────────────────────┬──────────────────────┬───────────────────────────────┤
│ 1. PUBLIC LAW STORE  │ 2. MATTER FACT STORE │ 3. CASE THEORY STORE          │
├──────────────────────┼──────────────────────┼───────────────────────────────┤
│ • Public statutes,   │ • Client leases,     │ • Attorney-asserted theories, │
│   ordinances, regs.  │   notices, photos.   │   judge tendencies, deadlines.│
│ • Versioned graph.   │ • Confidential.      │ • Project-scoped Lakebase.    │
│ • Permanent: never   │ • Cryptographic hard │ • Active superseding &        │
│   purged on matter   │   purge on matter    │   invalidation lifecycle.     │
│   close.             │   conclusion.        │ • Survives across sessions.   │
└──────────────────────┴──────────────────────┴───────────────────────────────┘
```

Retrieval is structured as an explicit relational join across stores:
$$\text{Issue} \longrightarrow \text{Candidate Authorities} \xrightarrow[\text{Matter Facts}]{\text{Filter}} \text{Controlling Rules} \xrightarrow[\text{Case Theory}]{\text{Synthesize}} \text{Grounded Claims}$$

---

## 5. End-to-End Walkthrough: California Residential Just-Cause Defense

Rather than attempting to solve all legal verticals simultaneously, the architecture is hardened against one exception-dense vertical: **California Residential Habitability, Security Deposits, and Just-Cause Evictions**.

### Matter Facts
- **Jurisdiction**: Oakland, CA.
- **Tenancy**: Multi-family apartment built in 1978.
- **Incident**: Landlord served a 30-day notice to terminate tenancy citing "owner move-in" for their adult nephew, without relocation assistance payment.

```
Step 1: Ingestion & Spatial Binding (KruschNexus)
  Input: `30_day_notice_to_vacate.pdf` (scanned exhibit).
  Processing: Poppler text layout extraction + Tesseract OCR on municipal notice stamp.
  Output: Stamped notice date, exact physical page 1, character offsets [120-450].
  Stored: Matter Evidence Store (isolated to matter_id = 104).

Step 2: Candidate Authority Retrieval (KruschLaw)
  Query: "owner move in eviction relative relocation payment"
  RRF Candidates:
    - OMC § 8.22.360 (Oakland Just Cause for Eviction Ordinance)
    - Cal. Civ. Code § 1946.2 (Statewide Tenant Protection Act)
    - OMC § 8.22.030 (Rent Adjustment Program Notice)

Step 3: Deterministic Jurisdiction Filtering
  - Matter location is incorporated Oakland. OMC § 8.22.360 applies.
  - Preemption check: Cal. Civ. Code § 1946.2(g)(1)(B) exempts cities with more 
    protective local just cause ordinances enacted before 2006. Oakland OMC § 8.22.360 
    is more protective. OMC controls.
  - Exception hydration: OMC § 8.22.360(A)(8) owner move-in allows only spouse, child, 
    parent, or grandparent. Nephew is excluded as a qualifying relative.

Step 4: Draft Generation & Claim-Level Entailment Gate
  Drafted Claim 1: "Under OMC § 8.22.360, an owner move-in eviction cannot be based on occupancy by a nephew."
    -> Pass A: OMC § 8.22.360 exists, active on incident date. PASS.
    -> Pass B: Bound to span OMC § 8.22.360(A)(8). Verdict: ENTAILED.
  
  Drafted Claim 2: "Landlord must pay relocation assistance within 10 days of notice."
    -> Pass A: OMC § 8.22.360 exists, active on incident date. PASS.
    -> Pass B: Bound to span OMC § 8.22.360(G). Verdict: CONTRADICTED.
       (Statute mandates half paid at service of notice, half upon vacating).
    -> Action: Claim 2 REFUSED. Downstream text corrected before attorney presentation.

Step 5: Working Memory Update (KruschContext MCP)
  Committed to Lakebase (`.agent/memory.db`):
  - Category: `lessons`
  - Content: "OMC § 8.22.360 OMI defense: nephew is non-qualifying relative; notice relocation payment split required under Subsection G."
```

---

## 6. The Amendment Pipeline: Stale-Law Review Queue

Statutory change is a product feature, not an edge case. When a municipal code or chaptered state bill is amended, the system must not silently rewrite legal conclusions. It executes an automated review pipeline:

```
[ New Legislative Ingestion ] ──► Compute AST / Section Diff against existing node
                                         │
                                         ▼
                                   Mark old node `status = amended`
                                   Set `superseded_by` pointer
                                         │
                                         ▼
                                   Query KruschContext for all active
                                   memories and matter findings citing old section
                                         │
                                         ▼
                                   Mark memories: `stale_pending_review`
                                         │
                                         ▼
                                   Surface Review Queue to Counsel:
                                   "3 saved matter theories may be invalidated"
```

---

## 7. Threat Model & Failure Boundaries

To prevent over-reliance on local agent architectures, operators must enforce defensive bounds against known failure vectors:

1. **Incomplete Municipal Corpus**: Municipal ordinances in small or unincorporated jurisdictions are frequently absent from public bulk archives. The system must fail closed: if a county code is missing, it must return `MISSING_GOVERNING_AUTHORITY` rather than falling back to state law assumptions.
2. **Degraded OCR Coordinates**: On third-generation photocopies or skewed scans, OCR bounding boxes may miscalculate character span offsets. Downstream claim binding must tag OCR spans with confidence scores.
3. **Missing Appellate Common Law**: Statutory text alone does not capture judicial gloss or binding appellate precedents. KruschLaw explicitly identifies that it indexes statutes and ordinances, not appellate case reporters.
4. **False Reliance on Grounded Falsehoods**: An agent can generate a grammatically flawless, fully grounded legal theory based on an incomplete factual narrative supplied by the client. Outputs remain provisional research work product requiring independent human review under California RPC Rule 1.1 and Rule 5.3.

---

## Conclusion: Engineering Bounds, Not Magic

True sovereignty in legal artificial intelligence is not achieved by declaring an architecture "private" or running open weights on localhost. 

It is achieved by:
- Bounding retrieval to **verified physical spans**.
- Bounding legal relevance to **deterministic jurisdictional and preemption rules**.
- Bounding generation to **independently verified, claim-level entailment**.
- Bounding working memory to **isolated, hygiene-enforced stores**.

When an agent operates within these constraints, it stops guessing what the law might be and provides attorneys with an auditable, verifiable research instrument.
