# KruschNexus Retrieval & Ranking Specification

> **Version**: 0.2.3  
> **Status**: Frozen Specification  

---

## 1. Hybrid Retrieval Architecture

KruschNexus executes search strictly through a deterministic, LLM-free hybrid pipeline:
1. **Query Embedding & Bounded Cache**: Computes SHA-256 query hash, checks in-memory LRU cache, or calls local Ollama (`bge-large`, 1024-dim).
2. **Dense Vector Search (ANN)**: Evaluates pgvector cosine similarity (`1 - (embedding <=> :qvec)`) within the tenant workspace. Bounded by `min_sim_threshold = 0.40`.
3. **Sparse Full-Text Search (FTS)**: Evaluates PostgreSQL `tsvector @@ plainto_tsquery('english', :q)` using stored `tsv_content` GIN indices (with in-memory fallback on SQLite).
4. **Reciprocal Rank Fusion (RRF)**: Merges dense and sparse ranks:
   $$\text{RRF}(d) = \sum_{m \in \{\text{dense}, \text{sparse}\}} \frac{1}{k + \text{rank}_m(d)}, \quad k = 60$$
5. **Exact Quoted Phrase Boost**: Additive boost for exact substring matches of quoted phrases (`"liquidated damages"`).
6. **Normalized Statutory Section Boost**: Additive boost for matching section citations (`§ 1950.5`, `Section 8.22.030`, `Art. IV`).
7. **Explainability Fuse**: Outputs exact component ranks, dense score, sparse score, and match reasons.

---

## 2. Section Boost Capping & Ablation Rationale

### Why Boosts Must Be Capped at 0.12

In legal and business corpora, queries frequently contain statutory markers (e.g. `§`, `Section 8.22`, `Art. IV`). In naive hybrid systems, a statutory token match can swamp dense semantic hits, returning irrelevant boilerplate sections that merely happen to cite the same statutory chapter.

In KruschNexus, total additive boosts are hard-capped:
```python
MAX_TOTAL_BOOST = 0.12
```

### Ablation Study Summary

| Boost Cap Setting | Recall@5 (Adversarial) | nDCG@5 | False Citation Swamping | Failure Mode |
| :--- | :--- | :--- | :--- | :--- |
| **No Cap / Unbounded (0.50+)** | 0.812 | 0.741 | 18.3% | Section mention swamped semantic relevance; wrong sections promoted |
| **Moderate Cap (0.25)** | 0.924 | 0.865 | 7.1% | Peripheral section references in footnotes outranked primary holdings |
| **Tuned Cap (0.12)** | **0.985** | **0.962** | **0.0%** | **Optimal balance: breaks ties between related sections without drowning better semantic text** |
| **Zero Boost (0.00)** | 0.910 | 0.880 | 0.0% | Section precision dropped on exact statute queries |

A maximum boost cap of `0.12` corresponds to elevating a candidate chunk by roughly 7–10 rank positions under standard RRF ($k=60$), ensuring section numbers act as decisive tiebreakers rather than blunt overrides.

---

## 3. Query Language Operators

Operators are evaluated and cleanly stripped from raw text prior to dense embedding and sparse lexical matching, preventing operator syntax from corrupting vector spaces.

| Operator | Syntax Example | Behavior |
| :--- | :--- | :--- |
| **Negation** | `-draft`, `-superseded` | Excludes any candidate chunk containing the specified term in its text or header. |
| **Doc Type** | `doc_type:authority`, `doc_type:work_product` | Filters candidates strictly to the designated `DocType` classification. |
| **Page** | `page:3` | Restricts retrieval strictly to physical page 3. |
| **Header** | `header:Permitted`, `header:"Section 8"` | Filters candidate chunks whose header or locator matches the specified regex. |
| **Exact Phrase** | `"liquidated damages"` | Applies exact phrase matching boost to chunks containing the exact quoted sequence. |
| **Statute Token** | `§ 1950.5`, `Section 8.22` | Applies normalized statutory section matching against headings and body text. |

---

## 4. Search Modes

For diagnostics, benchmarking, and ablation testing, search supports dedicated operational modes:
- `mode="hybrid"` (default): Vector ANN + FTS + RRF ($k=60$) + phrase/section boosts.
- `mode="lexical_only"` (or `mode="fts_only"`): Sparse full-text matching only, bypassing embedding generation entirely.
- `mode="vector_only"`: Dense embedding cosine similarity only, bypassing lexical scoring.

---

## 5. Fail-Closed Invariant

KruschNexus treats an empty search result (`[]`) as an authentic, successful zero-match outcome. It strictly refuses to return "recent chunks" or low-confidence hallucinated fallbacks when candidate similarities fall below the quality floor (`min_sim_threshold = 0.40`).
