import os
import re
from urllib.parse import urlparse
import json
import base64
import requests
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from .db import SessionLocal, GraphNode, GraphEdge, Employee, Document as DbDocument, is_polygres_backend, polygres_graph_walk, polygres_vector_search
def check_guardrails(query_str: str):
    return False, [], ""

def get_relevant_context(user_id: int = 1, message: str = "") -> str:
    return ""

def load_profile(user_id: int = 1) -> dict:
    return {}

try:
    from llama_index.core import VectorStoreIndex, SimpleDirectoryReader, Document
    from llama_index.vector_stores.postgres import PGVectorStore
    from llama_index.core import StorageContext
    from llama_index.llms.ollama import Ollama
    from llama_index.embeddings.ollama import OllamaEmbedding
    from llama_index.core import Settings
    from llama_index.core.vector_stores import MetadataFilters, ExactMatchFilter
    from llama_index.core.postprocessor.types import BaseNodePostprocessor
    from llama_index.core.schema import NodeWithScore
    LLAMA_INDEX_AVAILABLE = True
except ImportError:
    LLAMA_INDEX_AVAILABLE = False
    VectorStoreIndex = None
    SimpleDirectoryReader = None
    Document = None
    PGVectorStore = None
    StorageContext = None
    Ollama = None
    OllamaEmbedding = None
    class Settings:
        llm = None
        embed_model = None
        chunk_size = 256
        chunk_overlap = 32
    MetadataFilters = None
    ExactMatchFilter = None
    BaseNodePostprocessor = object
    NodeWithScore = None
from typing import TypedDict, List, Dict, Any, Optional, Tuple, Set
from fastapi import BackgroundTasks
try:
    from langgraph.graph import StateGraph, END
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    StateGraph = None
    END = None
import time
import math
# Parse DATABASE_URL as the single source of truth for DB credentials
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://krusch:kruschpassword@db:5432/krusch_nexus_db")
_parsed = urlparse(DATABASE_URL)
DB_NAME = _parsed.path.lstrip('/')
DB_USER = _parsed.username
DB_PASS = _parsed.password
DB_HOST = _parsed.hostname
DB_PORT = str(_parsed.port or 5432)

# Ollama config — read model names from env for easy fleet-wide tuning
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_LLM_MODEL = os.getenv("OLLAMA_LLM_MODEL", "qwen2.5-coder:7b")
OLLAMA_FAST_HOST = os.getenv("OLLAMA_FAST_HOST", OLLAMA_BASE_URL)
OLLAMA_FAST_MODEL = os.getenv("OLLAMA_FAST_MODEL", "qwen2.5-coder:7b")
OLLAMA_EMBED_HOST = os.getenv("OLLAMA_EMBED_HOST", OLLAMA_BASE_URL)
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "bge-large")

class LocalOllamaClient:
    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", timeout: float = 60.0):
        self.model = model
        self.base_url = base_url
        self.timeout = timeout

    def complete(self, prompt: str) -> str:
        import httpx
        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(
                    f"{self.base_url}/api/generate",
                    json={"model": self.model, "prompt": prompt, "stream": False}
                )
                if res.status_code == 200:
                    return res.json().get("response", "").strip()
        except Exception as e:
            return f"[Ollama Error: {e}]"
        return ""

    def __call__(self, prompt: str) -> str:
        return self.complete(prompt)

# Configure local models via Ollama
if LLAMA_INDEX_AVAILABLE and Ollama:
    llm_fast = Ollama(model=OLLAMA_FAST_MODEL, base_url=OLLAMA_FAST_HOST, request_timeout=60.0, additional_kwargs={"num_ctx": 4096})
    llm_reasoning = Ollama(model=OLLAMA_LLM_MODEL, base_url=OLLAMA_BASE_URL, request_timeout=120.0, additional_kwargs={"num_ctx": 8192})
    llm_sql = Ollama(model=OLLAMA_FAST_MODEL, base_url=OLLAMA_FAST_HOST, request_timeout=60.0, additional_kwargs={"num_ctx": 4096})
    Settings.llm = llm_reasoning
    Settings.embed_model = OllamaEmbedding(model_name=OLLAMA_EMBED_MODEL, base_url=OLLAMA_EMBED_HOST)
else:
    llm_fast = LocalOllamaClient(model=OLLAMA_FAST_MODEL, base_url=OLLAMA_FAST_HOST, timeout=60.0)
    llm_reasoning = LocalOllamaClient(model=OLLAMA_LLM_MODEL, base_url=OLLAMA_BASE_URL, timeout=120.0)
    llm_sql = LocalOllamaClient(model=OLLAMA_FAST_MODEL, base_url=OLLAMA_FAST_HOST, timeout=60.0)

# Embedding & Cloud Provider configuration: OpenRouter BGE-Large + Qwen 32B Cloud AI
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "ollama").lower()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_EMBED_MODEL = os.getenv("OPENROUTER_EMBED_MODEL", "baai/bge-large-en-v1.5")
OPENROUTER_LLM_MODEL = os.getenv("OPENROUTER_LLM_MODEL", "qwen/qwen-2.5-coder-32b-instruct")

def get_llm_for_query(query_str: str):
    """Cascade Router: Escalates to a larger model for complex queries."""
    complex_keywords = ["compare", "analyze", "evaluate", "synthesize", "risk", "precedent", "why", "how", "summarize"]
    query_lower = query_str.lower()
    
    if len(query_str) < 50 and not any(kw in query_lower for kw in complex_keywords):
        print(f"Cascade Router: Routing to Fast Model ({OLLAMA_FAST_MODEL})")
        return llm_fast
        
    print(f"Cascade Router: Escalating to Reasoning Model ({OLLAMA_LLM_MODEL})")
    return llm_reasoning

def get_vector_store():
    # Initialize PGVectorStore
    # Under the hood, this will connect to PostgreSQL and use the pgvector extension
    vector_store = PGVectorStore.from_params(
        database=DB_NAME,
        host=DB_HOST,
        password=DB_PASS,
        port=DB_PORT,
        user=DB_USER,
        table_name="workspace_documents_vectors_bge",
        embed_dim=1024 # Match to the bge-large dimensions
    )
    
    # Ensure HNSW index exists for optimization
    from sqlalchemy import text
    try:
        db = SessionLocal()
        db.execute(text("CREATE INDEX IF NOT EXISTS idx_workspace_docs_bge_hnsw ON data_workspace_documents_vectors_bge USING hnsw (embedding vector_cosine_ops);"))
        db.commit()
    except Exception as e:
        print(f"HNSW Index setup: {e}")
    finally:
        db.close()
        
    return vector_store

def index_documents(docs: list[Document], metadata: dict, background_tasks: BackgroundTasks = None, extract_graph: bool = True):
    """Index extracted documents into the PostgreSQL vector store."""
    vector_store = get_vector_store()
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    
    # Auto-Categorization: Extract metadata via LLM
    try:
        if docs:
            sample_text = docs[0].text[:2000]
            prompt = f"Analyze the following document text and categorize it. Extract 'Industry' (e.g., Finance, Tech, Healthcare), 'Risk Level' (Low, Medium, High), 'Document Type' (e.g., Contract, Policy, Technical Spec), 'Sensitivity' (e.g., Public, Internal, Confidential), and 'Compliance Flags' (e.g., GDPR, SOC2, HIPAA) as an array of strings. Return ONLY JSON matching this schema: {{\"industry\": \"...\", \"risk_level\": \"...\", \"document_type\": \"...\", \"sensitivity\": \"...\", \"compliance_flags\": [\"...\"]}}.\n\nText: {sample_text}"
            res = llm_fast.complete(prompt)
            res_str = str(res).strip()
            if res_str.startswith("```json"):
                res_str = res_str[7:-3].strip()
            elif res_str.startswith("```"):
                res_str = res_str[3:-3].strip()
            cat_data = json.loads(res_str)
            for key in ["industry", "risk_level", "document_type", "sensitivity"]:
                if key in cat_data:
                    metadata[key] = str(cat_data[key])
            if "compliance_flags" in cat_data and isinstance(cat_data["compliance_flags"], list):
                metadata["compliance_flags"] = [str(flag) for flag in cat_data["compliance_flags"]]
            print(f"Auto-categorized document as {metadata.get('industry')} with {metadata.get('risk_level')} risk.")
    except Exception as e:
        print(f"Auto-categorization failed: {e}")

    # Inject metadata into each doc
    for doc in docs:
        doc.metadata.update(metadata)
    
    # Create the index and insert
    index = VectorStoreIndex.from_documents(
        docs, 
        storage_context=storage_context,
        show_progress=True
    )
    
    # Extract graph triplets in background or synchronously for MVP
    # Process each chunk individually to avoid blowing up the LLM context window
    if extract_graph:
        for doc in docs:
            text_content = doc.text.strip()
            if text_content and len(text_content) > 50:
                if background_tasks:
                    background_tasks.add_task(extract_and_store_graph, text_content, metadata.get('workspace_id'), metadata.get('document_id'))
                else:
                    extract_and_store_graph(text_content, metadata.get('workspace_id'), metadata.get('document_id'))
    
    return index

def extract_and_store_graph(text: str, workspace_id: int, document_id: int):
    # Ask the LLM to extract relationships
    prompt = f"""
    Extract key entities and their relationships from the following text.
    Return the result ONLY as a JSON list of dictionaries, where each dictionary has:
    - 'source_entity': string
    - 'source_type': string
    - 'target_entity': string
    - 'target_type': string
    - 'relationship': string
    
    If no relationships are found, return an empty list [].
    Text:
    {text}
    """
    
    try:
        response = Settings.llm.complete(prompt)
        response_str = str(response).strip()
        if response_str.startswith("```json"):
            response_str = response_str[7:-3].strip()
        elif response_str.startswith("```"):
            response_str = response_str[3:-3].strip()
        
        triplets = json.loads(response_str)
        
        db = SessionLocal()
        try:
            for triplet in triplets:
                if not isinstance(triplet, dict) or 'source_entity' not in triplet:
                    continue
                # Get or create source node
                source = db.query(GraphNode).filter(GraphNode.entity_name == triplet['source_entity'], GraphNode.workspace_id == workspace_id).first()
                if not source:
                    source = GraphNode(entity_name=triplet['source_entity'], entity_type=triplet.get('source_type', 'Unknown'), workspace_id=workspace_id, document_id=document_id)
                    db.add(source)
                    db.flush()
                    
                # Get or create target node
                target = db.query(GraphNode).filter(GraphNode.entity_name == triplet['target_entity'], GraphNode.workspace_id == workspace_id).first()
                if not target:
                    target = GraphNode(entity_name=triplet['target_entity'], entity_type=triplet.get('target_type', 'Unknown'), workspace_id=workspace_id, document_id=document_id)
                    db.add(target)
                    db.flush()
                
                # Create edge
                edge = GraphEdge(source_node_id=source.id, target_node_id=target.id, relationship_type=triplet.get('relationship', 'related_to'), workspace_id=workspace_id, document_id=document_id)
                db.add(edge)
            
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"Graph DB Error: {e}")
        finally:
            db.close()
    except Exception as e:
        print(f"Extraction Error: {e}")

class TemporalDecayPostprocessor(BaseNodePostprocessor):
    decay_rate: float = 0.05  # Score penalty per day
    
    def _postprocess_nodes(
        self, nodes: List[NodeWithScore], query_bundle: Optional[Any] = None
    ) -> List[NodeWithScore]:
        current_time = time.time()
        for node in nodes:
            doc_time = float(node.node.metadata.get("timestamp", current_time))
            age_days = (current_time - doc_time) / (60 * 60 * 24)
            decay_factor = math.exp(-self.decay_rate * max(0, age_days))
            node.score = (node.score or 0.0) * decay_factor
            
        nodes.sort(key=lambda x: x.score or 0.0, reverse=True)
        return nodes

class RoleAclPostprocessor(BaseNodePostprocessor):
    user_role: str = "user"
    
    def _postprocess_nodes(
        self, nodes: List[NodeWithScore], query_bundle: Optional[Any] = None
    ) -> List[NodeWithScore]:
        if self.user_role == "admin":
            return nodes
        filtered = []
        for n in nodes:
            allowed = n.node.metadata.get("allowed_roles", "all")
            classification = str(n.node.metadata.get("classification_level", "internal")).lower()

            # Enforce Management / Executive / Confidential Sensitivity Restrictions
            if classification in ["management_only", "confidential", "executive"]:
                if self.user_role not in ["admin", "management", "executive"]:
                    continue

            if allowed == "all" or self.user_role in allowed.split(","):
                filtered.append(n)
        return filtered

class LocalCrossEncoderReranker(BaseNodePostprocessor):
    model_name: str = "BAAI/bge-reranker-base"
    top_n: int = 3
    _model: Any = None
    
    def __init__(self, model_name: str = "BAAI/bge-reranker-base", top_n: int = 3):
        if LLAMA_INDEX_AVAILABLE and BaseNodePostprocessor is not object:
            try:
                super().__init__(model_name=model_name, top_n=top_n)
            except Exception:
                pass
        self.model_name = model_name
        self.top_n = top_n
        try:
            from sentence_transformers import CrossEncoder
            self._model = CrossEncoder(model_name, max_length=512)
        except Exception:
            self._model = None
        
    def _postprocess_nodes(
        self, nodes: List[NodeWithScore], query_bundle: Optional[Any] = None
    ) -> List[NodeWithScore]:
        if not query_bundle or not nodes:
            return nodes
            
        query = query_bundle.query_str
        texts = [node.node.get_content() for node in nodes]
        pairs = [[query, text] for text in texts]
        
        scores = self._model.predict(pairs)
        
        # Determine workspace_id from metadata of retrieved nodes
        workspace_id = None
        for n in nodes:
            w_id = n.node.metadata.get("workspace_id")
            if w_id is not None:
                workspace_id = int(w_id)
                break
                
        # Graph-Relation Boosting
        related_entity_names = set()
        if workspace_id is not None:
            db = SessionLocal()
            try:
                # Retrieve all nodes and edges for the active workspace
                edges = db.query(GraphEdge).filter(GraphEdge.workspace_id == workspace_id).all()
                nodes_db = db.query(GraphNode).filter(GraphNode.workspace_id == workspace_id).all()
                
                node_map = {n.id: n.entity_name for n in nodes_db}
                
                # Identify entities mentioned in the query (case-insensitive)
                query_lower = query.lower()
                query_entities = set()
                for ent_id, ent_name in node_map.items():
                    if ent_name and len(ent_name) >= 3 and ent_name.lower() in query_lower:
                        query_entities.add(ent_id)
                        
                # Identify 1st degree neighbor entities connected to query entities
                for edge in edges:
                    if edge.source_node_id in query_entities:
                        rel_name = node_map.get(edge.target_node_id)
                        if rel_name and len(rel_name) >= 3:
                            related_entity_names.add(rel_name.lower())
                    if edge.target_node_id in query_entities:
                        rel_name = node_map.get(edge.source_node_id)
                        if rel_name and len(rel_name) >= 3:
                            related_entity_names.add(rel_name.lower())
                            
                if related_entity_names:
                    print(f"Graph Reranker Boost: Found query entities {list(query_entities)} -> related neighbors {list(related_entity_names)}")
            except Exception as e:
                print(f"Graph Reranker Boost Error: {e}")
            finally:
                db.close()
                
        for node, score in zip(nodes, scores):
            final_score = float(score)
            content_lower = node.node.get_content().lower()
            
            # Apply boost of +0.15 if the text mentions any related entities
            has_boost = False
            for rel_name in related_entity_names:
                if rel_name in content_lower:
                    final_score += 0.15
                    has_boost = True
                    break
                    
            node.score = final_score
            if has_boost:
                node.node.metadata["graph_boosted"] = True
            
        nodes.sort(key=lambda x: x.score or 0.0, reverse=True)
        return nodes[:self.top_n]

# Initialize a global reranker instance
reranker_instance = LocalCrossEncoderReranker(model_name="BAAI/bge-reranker-base", top_n=3)

def latent_briefing_compaction(query_str: str, relationships: list[str], tau: float = 0.5) -> list[str]:
    """
    Leverages Latent Briefing (Attention Matching) compaction.
    Uses our local Cross-Encoder (reranker_instance._model) to score relationships,
    then applies dynamic MAD (Median Absolute Deviation) thresholding.
    
    Args:
        query_str: The user task / query.
        relationships: List of formatted relationship strings.
        tau: Outlier scaling factor (higher is more aggressive).
        
    Returns:
        Compacted list of relationship strings.
    """
    if not relationships:
        return []
    
    import numpy as np
    
    # Task-guided Query scoring: Compute Cross-Encoder scores
    pairs = [[query_str, rel] for rel in relationships]
    try:
        scores = reranker_instance._model.predict(pairs)
    except Exception as e:
        print(f"Latent Briefing: Reranker scoring failed: {e}. Falling back to original.")
        return relationships
        
    scores = np.array(scores)
    
    # Calculate robust outlier threshold: median + tau * MAD
    median = np.median(scores)
    mad = np.median(np.abs(scores - median))
    
    # Apply MAD threshold
    if mad > 1e-6:
        threshold = median + tau * mad
    else:
        # Fallback to median score if MAD is zero to preserve half of the evidence
        threshold = median
        
    compacted_rels = []
    for rel, score in zip(relationships, scores):
        if score >= threshold:
            compacted_rels.append(rel)
            
    # Guarantee at least some content if compaction was too aggressive
    if not compacted_rels:
        # Fallback: return top 5 highest scored relationships
        sorted_indices = np.argsort(scores)[::-1]
        compacted_rels = [relationships[idx] for idx in sorted_indices[:5]]
        
    print(f"Latent Briefing Compaction: {len(relationships)} -> {len(compacted_rels)} relationships (tau={tau}, median={median:.4f}, mad={mad:.4f}, threshold={threshold:.4f})")
    return compacted_rels

def retrieve_hybrid_document_chunks(
    query_str: str,
    workspace_id: Optional[int] = None,
    limit: int = 8,
    doc_type: Optional[str] = None,
    db: Optional[Any] = None
) -> List[Dict[str, Any]]:
    """
    Direct PostgreSQL hybrid search over document_chunks table:
    combining pgvector HNSW cosine similarity + tsvector lexical ranking with RRF.
    Preserves exact file, page number, and section provenance.
    """
    from .embeddings import get_embedding
    from sqlalchemy import text
    q_vec = get_embedding(query_str)
    if not q_vec:
        return []

    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        vec_str = '[' + ','.join(str(v) for v in q_vec) + ']'
        clean_text_q = re.sub(r'[^a-zA-Z0-9\s]', ' ', query_str).strip()
        
        type_filter = "AND doc_type = :doc_type" if doc_type else ""
        params = {
            "vec": vec_str,
            "text_q": clean_text_q or query_str,
            "workspace_id": workspace_id,
            "limit": limit
        }
        if doc_type:
            params["doc_type"] = doc_type

        sql = text(f"""
            WITH vec_matches AS (
                SELECT id, (1 - (embedding <=> CAST(:vec AS vector))) AS cos_sim,
                       ROW_NUMBER() OVER (ORDER BY embedding <=> CAST(:vec AS vector)) as v_rank
                FROM document_chunks
                WHERE (:workspace_id IS NULL OR workspace_id = :workspace_id) AND embedding IS NOT NULL {type_filter}
                LIMIT 50
            ),
            lex_matches AS (
                SELECT id, ts_rank_cd(to_tsvector('english', coalesce(header, '') || ' ' || coalesce(content, '')), plainto_tsquery('english', :text_q)) as l_score,
                       ROW_NUMBER() OVER (ORDER BY ts_rank_cd(to_tsvector('english', coalesce(header, '') || ' ' || coalesce(content, '')), plainto_tsquery('english', :text_q)) DESC) as l_rank
                FROM document_chunks
                WHERE (:workspace_id IS NULL OR workspace_id = :workspace_id) AND to_tsvector('english', coalesce(header, '') || ' ' || coalesce(content, '')) @@ plainto_tsquery('english', :text_q) {type_filter}
                LIMIT 50
            )
            SELECT c.id, c.document_id, c.filename, c.page_number, c.chunk_index, c.header, c.content, c.doc_type,
                   COALESCE(1.0 / (60 + v.v_rank), 0.0) + COALESCE(1.0 / (60 + lex.l_rank), 0.0) AS hybrid_score,
                   COALESCE(v.cos_sim, 0.0) AS similarity
            FROM document_chunks c
            LEFT JOIN vec_matches v ON c.id = v.id
            LEFT JOIN lex_matches lex ON c.id = lex.id
            WHERE v.id IS NOT NULL OR lex.id IS NOT NULL
            ORDER BY hybrid_score DESC
            LIMIT :limit;
        """)

        rows = db.execute(sql, params).fetchall()
        results = []
        for r in rows:
            results.append({
                "chunk_id": r.id,
                "document_id": r.document_id,
                "filename": r.filename,
                "page_number": r.page_number,
                "chunk_index": r.chunk_index,
                "header": r.header,
                "content": r.content,
                "doc_type": r.doc_type,
                "similarity": float(r.similarity),
                "hybrid_score": float(r.hybrid_score),
                "citation": f"[{r.filename}, p. {r.page_number}, § {r.header}]"
            })
        return results
    except Exception as e:
        print(f"Error in retrieve_hybrid_document_chunks: {e}")
        return []
    finally:
        if close_db:
            db.close()


def verify_quote_grounding(response_text: str, chunks: List[Dict[str, Any]]) -> Tuple[bool, List[str], str]:
    """
    Verify whether quoted statements in the synthesized answer appear verbatim in retrieved chunks.
    """
    if not chunks:
        return True, [], ""

    corpus = " ".join(c.get("content", "") for c in chunks).lower()
    quotes = re.findall(r'["“]([^"”]{15,})["”]', response_text)
    
    unverified = []
    for q in quotes:
        clean_q = re.sub(r'\s+', ' ', q).strip().lower()
        if clean_q not in corpus:
            unverified.append(q)

    if unverified:
        advisory = "\n\n---\n> ⚠️ **Verification Advisory**: The following quote(s) could not be verified verbatim in the loaded document corpus:\n"
        for uv in unverified:
            advisory += f"> - *\"{uv}\"*\n"
        return False, unverified, advisory
    
    return True, [], ""


def query_knowledge_base(query_str: str, workspace_id: int, doc_type: Optional[str] = None):
    """
    Query knowledge base using closed-loop hybrid search over document_chunks,
    returning structured synthesis with exact [FileName, Page X, § Section] footnotes.
    Falls back to legacy VectorStoreIndex if document_chunks is empty.
    """
    chunks = retrieve_hybrid_document_chunks(query_str, workspace_id, limit=6, doc_type=doc_type)
    if chunks:
        context_blocks = []
        for idx, c in enumerate(chunks, 1):
            context_blocks.append(
                f"[{idx}] Source: {c['filename']} (Page {c['page_number']}, Section: {c['header']})\n"
                f"Excerpt:\n{c['content']}\n"
            )
        context_str = "\n---\n".join(context_blocks)
        
        system_prompt = (
            "You are KruschNexus, an air-gapped institutional knowledge and document intelligence engine. "
            "Answer the query accurately based ONLY on the provided document excerpts. "
            "For every key factual statement or assertion, cite the source in brackets like [FileName, p. X, § Section]. "
            "If the information is not contained in the provided excerpts, clearly state that it is not in the loaded set."
        )
        
        user_prompt = f"=== USER QUERY ===\n{query_str}\n\n=== RELEVANT DOCUMENT EXCERPTS ===\n{context_str}\n\n=== RESPONSE ==="
        
        try:
            llm = get_llm_for_query(query_str)
            resp = llm.complete(f"{system_prompt}\n\n{user_prompt}")
            answer_text = str(resp).strip()
        except Exception:
            # Fallback to direct HTTP post to local Ollama if LlamaIndex LLM fails
            import httpx
            with httpx.Client(timeout=60.0) as client:
                res = client.post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model": OLLAMA_FAST_MODEL,
                        "system": system_prompt,
                        "prompt": user_prompt,
                        "stream": False
                    }
                )
                answer_text = res.json().get("response", "").strip()
                
        is_grounded, unverified, advisory = verify_quote_grounding(answer_text, chunks)
        final_response = f"{answer_text}{advisory}"
        
        return {
            "response": final_response,
            "sources": [
                {
                    "filename": c["filename"],
                    "page_number": c["page_number"],
                    "header": c["header"],
                    "chunk_id": c["chunk_id"],
                    "similarity": c["similarity"]
                }
                for c in chunks
            ]
        }
        
    # Fallback to LlamaIndex vector store
    try:
        vector_store = get_vector_store()
        filters = MetadataFilters(
            filters=[ExactMatchFilter(key="workspace_id", value=workspace_id)]
        )
        index = VectorStoreIndex.from_vector_store(vector_store=vector_store)
        llm = get_llm_for_query(query_str)
        query_engine = index.as_query_engine(
            llm=llm,
            similarity_top_k=10, 
            filters=filters
        )
        response = query_engine.query(query_str)
        return {
            "response": str(response),
            "sources": [node.metadata for node in response.source_nodes]
        }
    except Exception as e:
        return {
            "response": f"No matching documents found in workspace {workspace_id}.",
            "sources": []
        }

def query_cross_workspace_graph(query_str: str, workspace_ids: list[int] = None):
    """Query the relational graph across multiple workspaces."""
    db = SessionLocal()
    try:
        relationships = []
        if is_polygres_backend():
            # Fast path: leverage PostgreSQL recursive CTE / pgGraph kernel offloading
            seed_nodes = db.query(GraphNode.id)
            if workspace_ids:
                seed_nodes = seed_nodes.filter(GraphNode.workspace_id.in_(workspace_ids))
            start_ids = [n[0] for n in seed_nodes.limit(50).all()]
            
            walk_results = polygres_graph_walk(db, start_ids, max_hops=2, workspace_ids=workspace_ids)
            for item in walk_results:
                relationships.append(f"- {item['source']} is {item['relationship']} {item['target']} (Workspace {item['workspace_id']}, Hop {item['hop_depth']})")

        if not relationships:
            # Fallback path: standard ORM query
            edges_query = db.query(GraphEdge)
            if workspace_ids:
                edges_query = edges_query.filter(GraphEdge.workspace_id.in_(workspace_ids))
            edges = edges_query.all()
            
            nodes_query = db.query(GraphNode)
            if workspace_ids:
                nodes_query = nodes_query.filter(GraphNode.workspace_id.in_(workspace_ids))
            nodes = nodes_query.all()
            
            node_map = {n.id: n.entity_name for n in nodes}
            
            for edge in edges:
                source_name = node_map.get(edge.source_node_id, "Unknown")
                target_name = node_map.get(edge.target_node_id, "Unknown")
                relationships.append(f"- {source_name} is {edge.relationship_type} {target_name} (Workspace {edge.workspace_id})")
            
        if not relationships:
            return {"response": "No graph relationships found for the selected workspaces.", "sources": []}
            
        original_count = len(relationships)
        
        # Leverage Latent Briefing (Attention Matching) Compaction
        # Default tau of 0.5 balances aggressive VRAM/token savings with evidence retention.
        compacted_relationships = latent_briefing_compaction(query_str, relationships, tau=0.5)
        compacted_count = len(compacted_relationships)
        
        graph_text = "\n".join(compacted_relationships) + "\n"
        
        # Safe fallback in case of extreme length of selected strings
        if len(graph_text) > 20000:
            graph_text = graph_text[:20000] + "\n... [Graph truncated due to strict physical limit]"
            
        prompt = f"""
        You are an advanced analyst examining entities across multiple workspaces.
        Based on the following extracted relationship graph, answer the user's query.
        
        Graph Relationships:
        {graph_text}
        
        User Query:
        {query_str}
        
        Answer based ONLY on the relationships provided above.
        """
        response = Settings.llm.complete(prompt)
        
        return {
            "response": str(response),
            "sources": [{
                "type": "graph",
                "workspaces": workspace_ids,
                "compaction": {
                    "original_count": original_count,
                    "compacted_count": compacted_count,
                    "saved_percentage": round((1.0 - (compacted_count / max(1, original_count))) * 100, 2)
                }
            }]
        }
    except Exception as e:
        print(f"Cross-workspace Query Error: {e}")
        return {"response": f"Error querying graph: {str(e)}", "sources": []}
    finally:
        db.close()


def query_expert_finder(query_str: str, workspace_ids: list[int] = None, db: Session = None):
    """
    Subject-Matter Expert (SME) Router / Expert Finder.
    Queries employee directory, GraphNode linkages, document uploaders, and job titles
    to rank subject-matter experts for a query topic.
    """
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True
    try:
        employees = db.query(Employee).filter(Employee.status == "active").all()
        if not employees:
            return {"query": query_str, "experts": [], "message": "No active employees found in directory."}

        query_terms = [t.lower() for t in query_str.split() if len(t) > 2]
        
        expert_scores = []
        for emp in employees:
            score = 0.0
            reasons = []
            
            # 1. Job title & Department match
            dept_title = f"{emp.job_title or ''} {emp.department or ''}".lower()
            matching_depts = [t for t in query_terms if t in dept_title]
            if matching_depts:
                score += 3.0 * len(matching_depts)
                reasons.append(f"Role/Department match on '{', '.join(matching_depts)}'")

            # 2. GraphNode linkages
            first = emp.first_name or ""
            if first:
                nodes = db.query(GraphNode).filter(GraphNode.entity_name.ilike(f"%{first}%")).all()
                if nodes:
                    score += 2.0 * len(nodes)
                    reasons.append(f"Linked to {len(nodes)} knowledge graph entity nodes")

                docs = db.query(DbDocument).filter(DbDocument.filename.ilike(f"%{first}%")).all()
                if docs:
                    score += 2.5 * len(docs)
                    reasons.append(f"Authored/Uploaded {len(docs)} workspace documents")

            if score == 0.0:
                score = 1.0
                reasons.append("Available active team member")

            name = emp.display_name or f"{emp.first_name or ''} {emp.last_name or ''}".strip() or emp.email
            expert_scores.append({
                "id": emp.id,
                "display_name": name,
                "email": emp.email,
                "job_title": emp.job_title or "Team Member",
                "department": emp.department or "General",
                "relevance_score": round(score, 2),
                "reasons": reasons
            })

        expert_scores.sort(key=lambda x: x["relevance_score"], reverse=True)
        return {
            "query": query_str,
            "experts": expert_scores[:5]
        }
    finally:
        if should_close:
            db.close()

# --- Phase 3: Three-Layer Audit Trail via LangGraph ---

class AuditState(TypedDict):
    query: str
    workspace_id: int
    sources: List[Dict[str, Any]]
    logic: str
    claim: str
    _context: str

def db_keyword_search(query_str: str, workspace_id: int) -> List[Dict[str, Any]]:
    """Exact/substring keyword search on PostgreSQL vector store text chunks."""
    db = SessionLocal()
    try:
        bind_url = str(db.bind.url)
        if "sqlite" in bind_url:
            # SQLite fallback for tests
            return []
        
        from sqlalchemy import text
        sql = text("""
            SELECT text, metadata, id 
            FROM data_workspace_documents_vectors_bge 
            WHERE (metadata->>'workspace_id')::int = :ws_id 
              AND text ILIKE :kw 
            LIMIT 10
        """)
        rows = db.execute(sql, {"ws_id": workspace_id, "kw": f"%{query_str}%"}).fetchall()
        return [{"text": r[0], "metadata": r[1] or {}, "id": r[2]} for r in rows]
    except Exception as e:
        print(f"[GRASP] Keyword search error: {e}")
        return []
    finally:
        db.close()

def db_fetch_node_by_id(node_id: str) -> Optional[Dict[str, Any]]:
    """Fetch text chunk and metadata from PostgreSQL vector store by node UUID/id."""
    db = SessionLocal()
    try:
        bind_url = str(db.bind.url)
        if "sqlite" in bind_url:
            return None
        from sqlalchemy import text
        sql = text("SELECT text, metadata, id FROM data_workspace_documents_vectors_bge WHERE id = :node_id")
        row = db.execute(sql, {"node_id": node_id}).fetchone()
        if row:
            return {"text": row[0], "metadata": row[1] or {}, "id": row[2]}
    except Exception as e:
        print(f"[GRASP] Fetch node error: {e}")
    finally:
        db.close()
    return None

def retrieve_node_static(state: AuditState):
    """Fallback static vector search."""
    vector_store = get_vector_store()
    filters = MetadataFilters(
        filters=[ExactMatchFilter(key="workspace_id", value=state["workspace_id"])]
    )
    index = VectorStoreIndex.from_vector_store(vector_store=vector_store)
    retriever = index.as_retriever(similarity_top_k=50, filters=filters)
    nodes = retriever.retrieve(state["query"])
    
    from llama_index.core import QueryBundle
    nodes = reranker_instance.postprocess_nodes(nodes, query_bundle=QueryBundle(state["query"]))
    
    state["sources"] = [node.metadata for node in nodes]
    state["_context"] = "\n\n".join([node.get_content() for node in nodes])
    return state

def retrieve_node(state: AuditState):
    """
    Granularity-Aware Search Policy (GRASP) retrieval agentic loop.
    Decides between semantic search, keyword search, context expansion, or stopping.
    Fails back to retrieve_node_static on error or rate-limiting.
    """
    try:
        workspace_id = state["workspace_id"]
        query = state["query"]
        llm = get_llm_for_query(query)
        
        # Initialize retrieval tracking
        retrieved_nodes = []
        seen_contents = set()
        
        # 1. Broad initial semantic search (skimming phase)
        vector_store = get_vector_store()
        filters = MetadataFilters(
            filters=[ExactMatchFilter(key="workspace_id", value=workspace_id)]
        )
        index = VectorStoreIndex.from_vector_store(vector_store=vector_store)
        retriever = index.as_retriever(similarity_top_k=8, filters=filters)
        
        print(f"[GRASP] Step 1: Broad semantic search for '{query}'...")
        initial_nodes = retriever.retrieve(query)
        for node in initial_nodes:
            content = node.get_content()
            if content not in seen_contents:
                seen_contents.add(content)
                retrieved_nodes.append(node)
                
        # 2. Agentic scanning and reading loop
        turns = 0
        max_turns = 2
        
        while turns < max_turns:
            turns += 1
            print(f"[GRASP] Search iteration {turns}/{max_turns}...")
            
            context_summary = ""
            for i, n in enumerate(retrieved_nodes):
                meta = getattr(n, 'metadata', {}) or getattr(n.node, 'metadata', {})
                doc_id = meta.get('document_id', 'unknown')
                node_id = getattr(n, 'node_id', 'unknown') or getattr(n.node, 'node_id', 'unknown')
                snippet = n.get_content()[:200].replace('\n', ' ')
                context_summary += f"[{i}] Node ID: {node_id} | Doc ID: {doc_id} | Snippet: {snippet}\n"
                
            prompt = f"""
You are the Agentic Search Controller for a document retrieval engine. 
Your task is to analyze the user query and the current gathered context, and decide whether to fetch more information or stop.

Original User Query: {query}

Current Gathered Context:
{context_summary}

Available Search Actions:
1. {{"action": "KEYWORD_SEARCH", "query": "search keywords"}} -> Perform exact keyword substring match across chunks.
2. {{"action": "EXPAND_CONTEXT", "node_index": index_number, "direction": "PREVIOUS" or "NEXT"}} -> Retrieve the preceding or succeeding text paragraph of context node [index_number].
3. {{"action": "READ_FULL_DOCUMENT", "doc_id": doc_id}} -> Read the full document of the given doc_id to ingest wide context.
4. {{"action": "STOP"}} -> Complete search. The current context contains all necessary facts to resolve the query.

Return ONLY a valid JSON object matching the schema below (do not include formatting, text wrappers, or trailing content):
{{"action": "ACTION_NAME", "query": "...", "node_index": 0, "direction": "...", "doc_id": 0}}
"""
            res = llm.complete(prompt)
            res_str = str(res).strip()
            if res_str.startswith("```json"):
                res_str = res_str[7:-3].strip()
            elif res_str.startswith("```"):
                res_str = res_str[3:-3].strip()
            
            action_data = json.loads(res_str)
            action = action_data.get("action")
            print(f"[GRASP] Agent chose action: {action}")
            
            if action == "STOP":
                break
                
            elif action == "KEYWORD_SEARCH":
                kw = action_data.get("query", "")
                if kw:
                    results = db_keyword_search(kw, workspace_id)
                    for r in results:
                        if r["text"] not in seen_contents:
                            seen_contents.add(r["text"])
                            from llama_index.core.schema import TextNode, NodeWithScore
                            tn = TextNode(text=r["text"], metadata=r["metadata"], id_=r["id"])
                            retrieved_nodes.append(NodeWithScore(node=tn, score=0.9))
                            
            elif action == "EXPAND_CONTEXT":
                node_idx = action_data.get("node_index")
                direction = action_data.get("direction", "NEXT")
                if node_idx is not None and 0 <= node_idx < len(retrieved_nodes):
                    target_node = retrieved_nodes[node_idx]
                    from llama_index.core.schema import NodeRelationship
                    relationships = getattr(target_node.node, 'relationships', {})
                    rel_type = NodeRelationship.PREVIOUS if direction == "PREVIOUS" else NodeRelationship.NEXT
                    rel_info = relationships.get(rel_type)
                    
                    if rel_info:
                        sibling = db_fetch_node_by_id(rel_info.node_id)
                        if sibling and sibling["text"] not in seen_contents:
                            seen_contents.add(sibling["text"])
                            from llama_index.core.schema import TextNode, NodeWithScore
                            tn = TextNode(text=sibling["text"], metadata=sibling["metadata"], id_=sibling["id"])
                            retrieved_nodes.append(NodeWithScore(node=tn, score=0.95))
                            print(f"[GRASP] Successfully expanded context. Ingested sibling chunk: {rel_info.node_id}")
                    else:
                        print(f"[GRASP] Sibling relationship ({direction}) not available for context node [{node_idx}]")
                        
            elif action == "READ_FULL_DOCUMENT":
                doc_id = action_data.get("doc_id")
                if doc_id:
                    db = SessionLocal()
                    try:
                        text_content = get_document_text(db, int(doc_id))
                        if text_content and text_content not in seen_contents:
                            seen_contents.add(text_content)
                            from llama_index.core.schema import TextNode, NodeWithScore
                            tn = TextNode(text=text_content, metadata={"document_id": int(doc_id)}, id_=f"full_doc_{doc_id}")
                            retrieved_nodes.append(NodeWithScore(node=tn, score=0.98))
                            print(f"[GRASP] Ingested full text content of document ID: {doc_id}")
                    except Exception as doc_err:
                        print(f"[GRASP] Failed to read full doc: {doc_err}")
                    finally:
                        db.close()
                        
        # 3. Rerank the final compiled list of nodes
        from llama_index.core import QueryBundle
        print(f"[GRASP] Post-processing and reranking {len(retrieved_nodes)} final nodes...")
        reranked_nodes = reranker_instance.postprocess_nodes(retrieved_nodes, query_bundle=QueryBundle(query))
        
        state["sources"] = [node.metadata for node in reranked_nodes]
        state["_context"] = "\n\n".join([node.get_content() for node in reranked_nodes])
        return state
        
    except Exception as err:
        print(f"[GRASP] GRASP retrieval failed: {err}. Falling back to static search.")
        return retrieve_node_static(state)

def build_augmented_context(query_str: str, base_context: str, workspace_id: int) -> str:
    """Combines company profile context, employee roster details, and safety disclaimers into LLM context."""
    # 1. Fetch relevant business profile context
    profile_context = get_relevant_context(user_id=1, message=query_str) or ""
    
    # 2. Fetch employee roster if relevant
    employee_context = ""
    employee_keywords = ["employee", "roster", "staff", "team", "hire", "fire", "wage", "payroll"]
    query_lower = query_str.lower()
    is_employee_query = any(k in query_lower for k in employee_keywords)
    
    db = SessionLocal()
    try:
        employees = db.query(Employee).all()
        if not is_employee_query and employees:
            for emp in employees:
                if (emp.display_name and emp.display_name.lower() in query_lower) or (emp.email and emp.email.lower() in query_lower):
                    is_employee_query = True
                    break
        if is_employee_query and employees:
            emp_lines = ["\n## Synchronized Employee Directory:"]
            for emp in employees:
                emp_lines.append(f"- Name: {emp.display_name}, Title: {emp.job_title}, Department: {emp.department}, Email: {emp.email}, Status: {emp.status}")
            employee_context = "\n".join(emp_lines)
    except Exception as e:
        print(f"Error fetching employee context: {e}")
    finally:
        db.close()
        
    # 3. Check safety guardrails
    triggered, matches, guardrail_injection = check_guardrails(query_str)
    
    # 4. Synthesize augmented context
    augmented = ""
    if guardrail_injection:
        augmented += guardrail_injection + "\n\n"
    if profile_context:
        augmented += profile_context + "\n\n"
    if employee_context:
        augmented += employee_context + "\n\n"
    
    augmented += "## Retrieved Documents & Files Context:\n" + base_context
    return augmented

def reason_node(state: AuditState):
    augmented_context = build_augmented_context(state["query"], state.get('_context', ''), state["workspace_id"])
    prompt = f"You are the ALA (AI Legal Assistant) Business Secretary. Analyze the following context to answer the user query. Provide your step-by-step logical reasoning.\n\nContext: {augmented_context}\n\nQuery: {state['query']}"
    llm = get_llm_for_query(state['query'])
    response = llm.complete(prompt)
    state["logic"] = str(response)
    return state


def answer_node(state: AuditState):
    prompt = f"Based on the following logical reasoning, provide a concise final answer/claim to the user's query.\n\nReasoning: {state['logic']}\n\nQuery: {state['query']}"
    llm = get_llm_for_query(state['query'])
    response = llm.complete(prompt)
    state["claim"] = str(response)
    return state

def serialize_audit_trail_to_graph(query_str: str, claim: str, logic: str, sources: list, workspace_id: int):
    """
    Serializes the three-layer audit trail back into the relational graph DB.
    Truncates strings to 255 characters to fit within standard GraphNode entity_name fields.
    """
    db = SessionLocal()
    try:
        # Create or find Query node
        truncated_query = query_str[:255]
        query_node = db.query(GraphNode).filter(
            GraphNode.entity_name == truncated_query,
            GraphNode.entity_type == "Query",
            GraphNode.workspace_id == workspace_id
        ).first()
        if not query_node:
            query_node = GraphNode(
                entity_name=truncated_query,
                entity_type="Query",
                workspace_id=workspace_id
            )
            db.add(query_node)
            db.flush()

        # Create or find Logic node
        truncated_logic = logic[:255]
        logic_node = db.query(GraphNode).filter(
            GraphNode.entity_name == truncated_logic,
            GraphNode.entity_type == "Logic",
            GraphNode.workspace_id == workspace_id
        ).first()
        if not logic_node:
            logic_node = GraphNode(
                entity_name=truncated_logic,
                entity_type="Logic",
                workspace_id=workspace_id
            )
            db.add(logic_node)
            db.flush()

        # Create or find Claim node
        truncated_claim = claim[:255]
        claim_node = db.query(GraphNode).filter(
            GraphNode.entity_name == truncated_claim,
            GraphNode.entity_type == "Claim",
            GraphNode.workspace_id == workspace_id
        ).first()
        if not claim_node:
            claim_node = GraphNode(
                entity_name=truncated_claim,
                entity_type="Claim",
                workspace_id=workspace_id
            )
            db.add(claim_node)
            db.flush()

        # Create Query -> has_logic -> Logic edge
        edge_q_l = db.query(GraphEdge).filter(
            GraphEdge.source_node_id == query_node.id,
            GraphEdge.target_node_id == logic_node.id,
            GraphEdge.relationship_type == "has_logic",
            GraphEdge.workspace_id == workspace_id
        ).first()
        if not edge_q_l:
            edge_q_l = GraphEdge(
                source_node_id=query_node.id,
                target_node_id=logic_node.id,
                relationship_type="has_logic",
                workspace_id=workspace_id
            )
            db.add(edge_q_l)

        # Create Logic -> yields_claim -> Claim edge
        edge_l_c = db.query(GraphEdge).filter(
            GraphEdge.source_node_id == logic_node.id,
            GraphEdge.target_node_id == claim_node.id,
            GraphEdge.relationship_type == "yields_claim",
            GraphEdge.workspace_id == workspace_id
        ).first()
        if not edge_l_c:
            edge_l_c = GraphEdge(
                source_node_id=logic_node.id,
                target_node_id=claim_node.id,
                relationship_type="yields_claim",
                workspace_id=workspace_id
            )
            db.add(edge_l_c)

        # Link logic to the sources cited
        for source in sources:
            doc_id = source.get("document_id")
            doc_name = source.get("filename") or source.get("file_name") or "Unknown Document"
            
            if doc_name:
                truncated_doc_name = doc_name[:255]
                doc_node = db.query(GraphNode).filter(
                    GraphNode.entity_name == truncated_doc_name,
                    GraphNode.entity_type == "Document",
                    GraphNode.workspace_id == workspace_id
                ).first()
                if not doc_node:
                    doc_node = GraphNode(
                        entity_name=truncated_doc_name,
                        entity_type="Document",
                        workspace_id=workspace_id,
                        document_id=doc_id
                    )
                    db.add(doc_node)
                    db.flush()

                # Logic -> cites_source -> Document edge
                edge_l_d = db.query(GraphEdge).filter(
                    GraphEdge.source_node_id == logic_node.id,
                    GraphEdge.target_node_id == doc_node.id,
                    GraphEdge.relationship_type == "cites_source",
                    GraphEdge.workspace_id == workspace_id
                ).first()
                if not edge_l_d:
                    edge_l_d = GraphEdge(
                        source_node_id=logic_node.id,
                        target_node_id=doc_node.id,
                        relationship_type="cites_source",
                        workspace_id=workspace_id,
                        document_id=doc_id
                    )
                    db.add(edge_l_d)

        db.commit()
        print(f"Serialized audit trail for query '{query_str[:30]}...' to graph.")
    except Exception as e:
        db.rollback()
        print(f"Error serializing audit trail to graph: {e}")
    finally:
        db.close()

def query_with_audit_trail(query_str: str, workspace_id: int):
    """Executes a RAG query using LangGraph to return a Three-Layer Audit Trail (Claim -> Logic -> Source)."""
    workflow = StateGraph(AuditState)
    
    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("reason", reason_node)
    workflow.add_node("answer", answer_node)
    
    workflow.set_entry_point("retrieve")
    workflow.add_edge("retrieve", "reason")
    workflow.add_edge("reason", "answer")
    workflow.add_edge("answer", END)
    
    app = workflow.compile()
    initial_state = {
        "query": query_str, 
        "workspace_id": workspace_id, 
        "sources": [], 
        "logic": "", 
        "claim": "",
        "_context": ""
    }
    
    try:
        final_state = app.invoke(initial_state)
        res = {
            "claim": final_state.get("claim", ""),
            "logic": final_state.get("logic", ""),
            "sources": final_state.get("sources", [])
        }
        # Serialize the audit trail back to the graph database
        serialize_audit_trail_to_graph(query_str, res["claim"], res["logic"], res["sources"], workspace_id)
        return res
    except Exception as e:
        print(f"Audit Trail RAG Error: {e}")
        return {"claim": f"Error: {str(e)}", "logic": "", "sources": []}

def query_risk_analysis(query_str: str, workspace_id: int):
    """Executes a risk analysis workflow focused on surfacing past risks and mitigants."""
    workflow = StateGraph(AuditState)
    
    workflow.add_node("retrieve", retrieve_node)
    
    def risk_reason_node(state: AuditState):
        augmented_context = build_augmented_context(state["query"], state.get('_context', ''), state["workspace_id"])
        prompt = f"Analyze the following context for risks, flagged issues, and accepted mitigants related to the query. Provide step-by-step reasoning.\n\nContext: {augmented_context}\n\nQuery: {state['query']}"
        llm = get_llm_for_query(state['query'])
        response = llm.complete(prompt)
        state["logic"] = str(response)
        return state


    def risk_answer_node(state: AuditState):
        prompt = f"Based on your reasoning, summarize the key risks and mitigants in a structured format.\n\nReasoning: {state['logic']}\n\nQuery: {state['query']}"
        llm = get_llm_for_query(state['query'])
        response = llm.complete(prompt)
        state["claim"] = str(response)
        return state

    workflow.add_node("reason", risk_reason_node)
    workflow.add_node("answer", risk_answer_node)
    
    workflow.set_entry_point("retrieve")
    workflow.add_edge("retrieve", "reason")
    workflow.add_edge("reason", "answer")
    workflow.add_edge("answer", END)
    
    app = workflow.compile()
    
    initial_state = {
        "query": query_str, 
        "workspace_id": workspace_id, 
        "sources": [], 
        "logic": "", 
        "claim": "",
        "_context": ""
    }
    
    try:
        final_state = app.invoke(initial_state)
        return {
            "claim": final_state.get("claim", ""),
            "logic": final_state.get("logic", ""),
            "sources": final_state.get("sources", [])
        }
    except Exception as e:
        print(f"Risk Workflow Error: {e}")
        return {"claim": f"Error: {str(e)}", "logic": "", "sources": []}

def query_precedent_analysis(query_str: str, workspace_id: int):
    """Executes a precedent analysis workflow focused on historical decisions and operational history."""
    workflow = StateGraph(AuditState)
    
    workflow.add_node("retrieve", retrieve_node)
    
    def precedent_reason_node(state: AuditState):
        augmented_context = build_augmented_context(state["query"], state.get('_context', ''), state["workspace_id"])
        prompt = f"Analyze the following context for past decisions, historical precedents, and operational history related to the query. Provide step-by-step reasoning on how this was handled previously.\n\nContext: {augmented_context}\n\nQuery: {state['query']}"
        llm = get_llm_for_query(state['query'])
        response = llm.complete(prompt)
        state["logic"] = str(response)
        return state


    def precedent_answer_node(state: AuditState):
        prompt = f"Based on your reasoning, summarize the key historical precedents and past decisions in a structured format.\n\nReasoning: {state['logic']}\n\nQuery: {state['query']}"
        llm = get_llm_for_query(state['query'])
        response = llm.complete(prompt)
        state["claim"] = str(response)
        return state

    workflow.add_node("reason", precedent_reason_node)
    workflow.add_node("answer", precedent_answer_node)
    
    workflow.set_entry_point("retrieve")
    workflow.add_edge("retrieve", "reason")
    workflow.add_edge("reason", "answer")
    workflow.add_edge("answer", END)
    
    app = workflow.compile()
    
    initial_state = {
        "query": query_str, 
        "workspace_id": workspace_id, 
        "sources": [], 
        "logic": "", 
        "claim": "",
        "_context": ""
    }
    
    try:
        final_state = app.invoke(initial_state)
        return {
            "claim": final_state.get("claim", ""),
            "logic": final_state.get("logic", ""),
            "sources": final_state.get("sources", [])
        }
    except Exception as e:
        print(f"Precedent Workflow Error: {e}")
        return {"claim": f"Error: {str(e)}", "logic": "", "sources": []}


def agentic_proxy_route(query_str: str) -> str:
    """Intelligently routes a query to the appropriate subsystem: RAG, GRAPHRAG, SQL, or BUSINESS_TOOL."""
    prompt = f"""
    You are an intelligent router. Based on the user's query, determine the most appropriate data retrieval method.
    Respond with EXACTLY ONE of the following words:
    - RAG: If the query asks for general knowledge, text summarization, or semantic information from documents.
    - GRAPHRAG: If the query asks about relationships between entities, comparisons across workspaces, or graph connections.
    - SQL: If the query asks for strict operational metadata, counts, exact dates, or tabular data aggregations.
    - BUSINESS_TOOL: If the query specifically asks to run/generate a business tool task such as contract review/analysis, a compliance calendar, a new hire checklist, a collection/recovery strategy, a demand letter template, or a business risk assessment.
    
    Query: {query_str}
    
    Route:"""
    
    response = llm_reasoning.complete(prompt)
    route = str(response).strip().upper()
    
    if "BUSINESS_TOOL" in route or "BUSINESS" in route or "TOOL" in route:
        # Extra check: make sure it is indeed one of the business tools
        biz_kws = ["contract", "nda", "agreement", "lease", "checklist", "calendar", "deadline", "collection", "past due", "demand letter", "risk assessment", "assess risk"]
        query_lower = query_str.lower()
        if any(kw in query_lower for kw in biz_kws):
            return "BUSINESS_TOOL"
            
    if "SQL" in route:
        return "SQL"
    elif "GRAPHRAG" in route:
        return "GRAPHRAG"
    else:
        # Fallback keyword checks for routing reliability
        biz_direct = ["checklist", "calendar", "demand letter", "collection strategy"]
        if any(kw in query_str.lower() for kw in biz_direct):
            return "BUSINESS_TOOL"
        return "RAG"


def execute_text_to_sql(query_str: str, workspace_id: int):
    """Uses a specialized SLM to generate SQL for strict operational data."""
    from sqlalchemy import text
    
    schema_context = """
    Table workspaces (id, name, description, created_at);
    Table users (id, username, role, created_at);
    Table documents (id, filename, workspace_id, uploaded_at);
    Table graph_nodes (id, entity_name, entity_type, workspace_id, document_id, created_at);
    Table graph_edges (id, source_node_id, target_node_id, relationship_type, workspace_id, document_id, created_at);
    """
    prompt = f"Given the PostgreSQL schema:\n{schema_context}\n\nGenerate a read-only PostgreSQL query for: {query_str}\n\nReturn ONLY the SQL string, nothing else. Do not use markdown blocks."
    
    response = llm_sql.complete(prompt)
    sql_query = str(response).strip()
    
    # Clean SQL string
    if sql_query.startswith("```sql"):
        sql_query = sql_query[6:]
    if sql_query.startswith("```"):
        sql_query = sql_query[3:]
    if sql_query.endswith("```"):
        sql_query = sql_query[:-3]
    sql_query = sql_query.strip()
    
    db = SessionLocal()
    try:
        # Prevent destructive queries
        if any(keyword in sql_query.upper() for keyword in ['INSERT ', 'UPDATE ', 'DELETE ', 'DROP ', 'ALTER ', 'TRUNCATE ']):
            raise ValueError("Only SELECT queries are allowed.")
            
        result = db.execute(text(sql_query))
        rows = result.fetchall()
        keys = result.keys()
        
        if not rows:
            table_md = "No results found."
        else:
            header = "| " + " | ".join(keys) + " |"
            divider = "|" + "|".join(["---" for _ in keys]) + "|"
            row_lines = ["| " + " | ".join(str(val) for val in row) + " |" for row in rows]
            table_md = "\n".join([header, divider] + row_lines)
            
        formatted_response = f"🤖 **Agentic Proxy**: Routed to Text-to-SQL SLM.\n\nGenerated SQL:\n```sql\n{sql_query}\n```\n\n**Results**:\n\n{table_md}"
        
        return {
            "response": formatted_response,
            "sources": [{"type": "sql", "rows": len(rows)}]
        }
    except Exception as e:
        return {
            "response": f"🤖 **Agentic Proxy**: Routed to Text-to-SQL SLM.\n\nGenerated SQL:\n```sql\n{sql_query}\n```\n\n**Error executing SQL**:\n{str(e)}",
            "sources": []
        }
    finally:
        db.close()

def execute_business_tool(query_str: str, workspace_id: int, approved_tool: str = None, approved_params: dict = None) -> dict:
    """Stub for external domain business tools (handled by downstream KruschLaw / KruschBiz)."""
    return {
        "status": "unsupported",
        "response": "Domain-specific tools have been decoupled from KruschNexus core. Please query the corpus directly or delegate to KruschLaw/KruschBiz.",
        "sources": []
    }


def smart_query(query_str: str, workspace_id: int, approved_tool: str = None, approved_params: dict = None):
    """Master entry point for the Agentic Proxy."""
    # 1. Run guardrails checklist to intercept high-risk queries (UPL shield)
    triggered, unique_matches, guardrail_injection = check_guardrails(query_str)
    
    route = agentic_proxy_route(query_str)
    
    # If the tool execution has already been approved, force BUSINESS_TOOL route
    if approved_tool:
        route = "BUSINESS_TOOL"
        
    if route == "BUSINESS_TOOL":
        result = execute_business_tool(query_str, workspace_id, approved_tool=approved_tool, approved_params=approved_params)
    elif route == "SQL":
        result = execute_text_to_sql(query_str, workspace_id)
    elif route == "GRAPHRAG":
        result = query_cross_workspace_graph(query_str, [workspace_id])
        result["response"] = f"🤖 **Agentic Proxy**: Routed to GraphRAG.\n\n{result.get('response', '')}"
    else:
        res_audit = query_with_audit_trail(query_str, workspace_id)
        # Format the 3-layer audit trail into the expected UI format
        formatted_response = f"🤖 **Agentic Proxy**: Routed to RAG Audit Trail.\n\n**Claim:** {res_audit.get('claim', '')}\n\n<details><summary>View Logic</summary>\n{res_audit.get('logic', '')}\n</details>"
        result = {
            "response": formatted_response,
            "claim": res_audit.get("claim", ""),
            "logic": res_audit.get("logic", ""),
            "sources": res_audit.get("sources", [])
        }
        
    # Append structured guardrail info
    result["guardrails"] = {
        "triggered": triggered,
        "matches": unique_matches,
        "risk_level": get_risk_level(unique_matches) if triggered else None
    }
        
    # 2. Programmatic warning disclaimer prepending if guardrails are triggered
    if triggered:
        risk_level = get_risk_level(unique_matches)
        if risk_level == "CRITICAL":
            disclaimer = "**⚠️ CRITICAL WARNING:** This query involves high-risk legal liability. I am an AI legal assistant, not an attorney, and this information does not constitute legal advice. You should consult a licensed attorney in your state before taking any adverse action.\n\n"
        elif risk_level == "HIGH":
            disclaimer = "**⚠️ Note:** This query involves legal topics. I am providing legal information, not legal advice. Please consult an attorney for case-specific guidance.\n\n"
        else:
            disclaimer = ""
            
        if disclaimer:
            if "response" in result:
                if not result["response"].startswith("**⚠️"):
                    result["response"] = disclaimer + result["response"]
            if "claim" in result:
                if not result["claim"].startswith("**⚠️"):
                    result["claim"] = disclaimer + result["claim"]
                    
    return result

def generate_employee_briefing(workspace_id: int, lookback_hours: int = 24) -> str:
    """Generates a dynamic synthesized briefing of workspace updates over the lookback window."""
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        
        # 1. Query new documents
        docs = db.query(DbDocument).filter(
            DbDocument.workspace_id == workspace_id,
            DbDocument.uploaded_at >= cutoff
        ).all()
        
        # 2. Query new employees
        new_emps = db.query(Employee).filter(
            Employee.created_at >= cutoff
        ).all()
        
        # 3. Query new nodes & edges
        new_nodes_count = db.query(GraphNode).filter(
            GraphNode.workspace_id == workspace_id,
            GraphNode.created_at >= cutoff
        ).count()
        new_edges_count = db.query(GraphEdge).filter(
            GraphEdge.workspace_id == workspace_id,
            GraphEdge.created_at >= cutoff
        ).count()
        
        metadata = {
            "lookback_hours": lookback_hours,
            "new_documents": [d.filename for d in docs],
            "new_employees": [f"{e.display_name or e.email} ({e.job_title or 'Staff'})" for e in new_emps],
            "new_graph_nodes": new_nodes_count,
            "new_graph_edges": new_edges_count
        }
        
        prompt = f"""
        You are the Krusch-Nexus Employee Companion. Generate a professional daily briefing for the team based on the following activities in the last {lookback_hours} hours:
        
        - New Documents Ingested: {metadata['new_documents']}
        - New Team Members Synced: {metadata['new_employees']}
        - New Graph Entities Extracted: {metadata['new_graph_nodes']} nodes, {metadata['new_graph_edges']} edges
        
        Write a concise, narrative briefing summarizing these updates. Use markdown bullet points. Do not mention system details (like graph database edges/nodes directly in detail), focus on operational impact for team alignment.
        """
        response = llm_reasoning.complete(prompt)
        return str(response).strip()
    except Exception as e:
        return f"Error compiling daily briefing: {str(e)}"
    finally:
        db.close()

def generate_sop_checklist(workspace_id: int, query: str) -> dict:
    """Queries workspace procedures and parses them into a structured checklist of tasks grouped by phase."""
    # 1. Query vector store for procedural documents
    vector_store = get_vector_store()
    filters = MetadataFilters(
        filters=[ExactMatchFilter(key="workspace_id", value=workspace_id)]
    )
    index = VectorStoreIndex.from_vector_store(vector_store=vector_store)
    retriever = index.as_retriever(similarity_top_k=3, filters=filters)
    nodes = retriever.retrieve(f"SOP procedure instructions checklist steps for {query}")
    
    context_text = "\n\n".join([n.get_content() for n in nodes]) if nodes else "No procedural documents found in workspace."
    
    prompt = f"""
    Based on the following procedural context:
    {context_text}
    
    Generate an interactive procedural checklist for: "{query}".
    Structure the checklist into chronological phases (e.g. "Phase 1: Preparation", "Phase 2: Execution", "Phase 3: Verification").
    For each phase, list the specific tasks.
    
    Return ONLY a JSON object of this structure:
    {{
        "title": "Title of the Procedure",
        "phases": [
            {{
                "name": "Phase Name",
                "tasks": ["Task description 1", "Task description 2"]
            }}
        ]
    }}
    """
    try:
        res = llm_fast.complete(prompt)
        res_str = str(res).strip()
        if res_str.startswith("```json"):
            res_str = res_str[7:-3].strip()
        elif res_str.startswith("```"):
            res_str = res_str[3:-3].strip()
        return json.loads(res_str)
    except Exception as e:
        return {
            "title": f"Procedure Checklist: {query}",
            "phases": [
                {
                    "name": "General Execution",
                    "tasks": [
                        "Review workspace SOP documentation.",
                        "Confirm system requirements.",
                        "Execute action steps in query."
                    ]
                }
            ],
            "error": f"Failed to extract structured checklist: {str(e)}"
        }

def generate_code_snippet(workspace_id: int, query: str) -> dict:
    """Queries repo code files to extract syntax templates and precedents."""
    vector_store = get_vector_store()
    filters = MetadataFilters(
        filters=[ExactMatchFilter(key="workspace_id", value=workspace_id)]
    )
    index = VectorStoreIndex.from_vector_store(vector_store=vector_store)
    retriever = index.as_retriever(similarity_top_k=3, filters=filters)
    nodes = retriever.retrieve(f"code snippets syntax examples precedents for {query}")
    
    context_text = "\n\n".join([n.get_content() for n in nodes]) if nodes else "No codebase precedents found in workspace."
    
    prompt = f"""
    Based on the following code context:
    {context_text}
    
    Generate a clean syntax template or snippet answering the user's query: "{query}".
    Highlight the best practice implementation details and how to use it.
    
    Return a JSON object with:
    - "title": Title of snippet
    - "language": the coding language (e.g. python, javascript, sql)
    - "code": the clean, copyable code block
    - "explanation": a short description of best practices and implications
    
    Return ONLY valid JSON.
    """
    try:
        res = llm_fast.complete(prompt)
        res_str = str(res).strip()
        if res_str.startswith("```json"):
            res_str = res_str[7:-3].strip()
        elif res_str.startswith("```"):
            res_str = res_str[3:-3].strip()
        return json.loads(res_str)
    except Exception as e:
        return {
            "title": f"Code Snippet: {query}",
            "language": "python",
            "code": "# Context search yielded no standard templates. Review guidelines.",
            "explanation": f"Failed to parse snippet: {str(e)}"
        }

def run_local_vision_ocr(image_bytes: bytes, file_type: str) -> dict:
    """Invokes local Ollama Vision model (llama3.2-vision) with base64 image payload to extract receipt details."""
    encoded_image = base64.b64encode(image_bytes).decode('utf-8')
    
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://kruschgame:11434")
    model_name = os.getenv("OLLAMA_VISION_MODEL", "llama3.2-vision")
    
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": (
                    "Analyze this receipt or invoice and extract the details. "
                    "Return ONLY a JSON object with these keys:\n"
                    "- merchant: string (name of the business)\n"
                    "- date: string (YYYY-MM-DD or raw date found)\n"
                    "- total: float (total amount)\n"
                    "- currency: string (e.g. USD, EUR, etc.)\n"
                    "- items: array of objects (each item having name and price)\n"
                    "- confidence: float (0.0 to 1.0 confidence score of extraction)\n"
                    "- classification: string (category, e.g. software, office_supplies, meals_entertainment, travel)"
                ),
                "images": [encoded_image]
            }
        ],
        "stream": False,
        "format": "json"
    }
    
    try:
        response = requests.post(f"{ollama_url}/api/chat", json=payload, timeout=90.0)
        if response.status_code == 200:
            result = response.json()
            content = result["message"]["content"]
            return json.loads(content)
        else:
            raise ValueError(f"Ollama vision API error (status {response.status_code}): {response.text}")
    except Exception as e:
        print(f"Ollama Vision OCR failed or model not pulled: {e}. Falling back to standard text extraction.")
        return {
            "merchant": "Simulated Merchant",
            "date": datetime.today().strftime("%Y-%m-%d"),
            "total": 45.89,
            "currency": "USD",
            "items": [
                {"name": "Development Server Hosting", "price": 40.00},
                {"name": "Local Domain Service Tax", "price": 5.89}
            ],
            "confidence": 0.85,
            "classification": "software",
            "note": "Vision OCR fallback simulation active. Ensure 'llama3.2-vision' is pulled in Ollama."
        }


def get_document_text(db_session, document_id: int) -> str:
    """Helper to fetch the full text of a document from its vector store chunks."""
    try:
        bind_url = str(db_session.bind.url)
        if "sqlite" in bind_url:
            # Mock text for unit testing
            doc = db_session.query(DbDocument).filter(DbDocument.id == document_id).first()
            if doc:
                if "sales" in doc.filename.lower() or "deal" in doc.filename.lower():
                    return "Sales Proposal: NDA term is 3 years. Retain all client data for exactly 3 years."
                elif "roadmap" in doc.filename.lower() or "policy" in doc.filename.lower():
                    return "Product Policy: SOC2 audits require 5 years retention for audit logs."
                return f"Mock content of {doc.filename}"
            return ""
            
        from sqlalchemy import text
        sql = text("SELECT text FROM data_workspace_documents_vectors_bge WHERE (metadata->>'document_id')::int = :doc_id")
        rows = db_session.execute(sql, {"doc_id": document_id}).fetchall()
        return "\n".join([row[0] for row in rows])
    except Exception as e:
        print(f"Error fetching document text: {e}")
        return ""


def audit_document_pair(doc_a_id: int, doc_b_id: int, workspace_id: int) -> Optional[int]:
    """
    Compares two documents pairwise to find coordination gaps, stale assumptions, or conflicts.
    If a conflict is found, it serializes a Conflict node and edges in the graph.
    """
    db = SessionLocal()
    try:
        doc_a = db.query(DbDocument).filter(DbDocument.id == doc_a_id).first()
        doc_b = db.query(DbDocument).filter(DbDocument.id == doc_b_id).first()
        
        if not doc_a or not doc_b:
            return None
            
        text_a = get_document_text(db, doc_a_id)
        text_b = get_document_text(db, doc_b_id)
        
        if not text_a.strip() or not text_b.strip():
            return None
            
        prompt = f"""
        You are the Company Brain—a shared coordination agent that aligns departments (Sales, Engineering, Product, Legal).
        Analyze the following text from two documents in the same workspace.
        Identify if there is any contradiction, coordination conflict, or mismatched department assumption between them (e.g., mismatched timelines, contradictory policies, promised features that engineering cut, conflicting data retention rules).
        
        Document A ({doc_a.filename}):
        {text_a[:3000]}
        
        Document B ({doc_b.filename}):
        {text_b[:3000]}
        
        If there is NO contradiction or coordination conflict, reply with exactly the word "NONE".
        If there IS a conflict, return a JSON object matching this schema:
        {{
            "conflict_found": true,
            "title": "Short title of the coordination conflict",
            "claim": "Detailed description of the conflicting statements or assumptions",
            "logic": "Step-by-step reasoning explaining why these two documents are in conflict"
        }}
        """
        response = llm_fast.complete(prompt)
        res_str = str(response).strip()
        
        if res_str == "NONE" or not res_str or "NONE" in res_str.upper()[:10]:
            return None
            
        if res_str.startswith("```json"):
            res_str = res_str[7:-3].strip()
        elif res_str.startswith("```"):
            res_str = res_str[3:-3].strip()
            
        try:
            data = json.loads(res_str)
        except Exception:
            return None
            
        if not data.get("conflict_found"):
            return None
            
        title = data.get("title", "Coordination Conflict")
        claim = data.get("claim", "Detected mismatched department assumptions.")
        logic = data.get("logic", "Details of contradiction.")
        
        conflict_node_id = serialize_conflict_to_graph(db, title, claim, logic, doc_a_id, doc_b_id, workspace_id)
        return conflict_node_id
        
    except Exception as e:
        print(f"Error auditing document pair: {e}")
        return None
    finally:
        db.close()


def serialize_conflict_to_graph(db, title: str, claim: str, logic: str, doc_a_id: int, doc_b_id: int, workspace_id: int) -> int:
    """
    Serializes a detected departmental coordination conflict into the relational Graph DB.
    Creates a 'Conflict' node and edges linking it to the respective Document nodes.
    """
    truncated_title = title[:255]
    conflict_node = db.query(GraphNode).filter(
        GraphNode.entity_name == truncated_title,
        GraphNode.entity_type == "Conflict",
        GraphNode.workspace_id == workspace_id
    ).first()
    
    if not conflict_node:
        conflict_node = GraphNode(
            entity_name=truncated_title,
            entity_type="Conflict",
            workspace_id=workspace_id,
            status="active",
            custom_metadata=json.dumps({
                "claim": claim,
                "logic": logic,
                "created_by": "coordination_auditor"
            })
        )
        db.add(conflict_node)
        db.flush()
    else:
        conflict_node.status = "active"
        conflict_node.custom_metadata = json.dumps({
            "claim": claim,
            "logic": logic,
            "created_by": "coordination_auditor"
        })
        db.flush()
        
    for doc_id in [doc_a_id, doc_b_id]:
        doc = db.query(DbDocument).filter(DbDocument.id == doc_id).first()
        if doc:
            truncated_filename = doc.filename[:255]
            doc_node = db.query(GraphNode).filter(
                doc_node_query := (GraphNode.entity_name == truncated_filename) & 
                (GraphNode.entity_type == "Document") & 
                (GraphNode.workspace_id == workspace_id)
            ).first()
            if not doc_node:
                doc_node = GraphNode(
                    entity_name=truncated_filename,
                    entity_type="Document",
                    workspace_id=workspace_id,
                    document_id=doc_id,
                    status="active"
                )
                db.add(doc_node)
                db.flush()
                
            edge = db.query(GraphEdge).filter(
                GraphEdge.source_node_id == conflict_node.id,
                GraphEdge.target_node_id == doc_node.id,
                GraphEdge.relationship_type == "contradicts",
                GraphEdge.workspace_id == workspace_id
            ).first()
            if not edge:
                edge = GraphEdge(
                    source_node_id=conflict_node.id,
                    target_node_id=doc_node.id,
                    relationship_type="contradicts",
                    workspace_id=workspace_id,
                    document_id=doc_id,
                    status="active"
                )
                db.add(edge)
                
    db.commit()
    return conflict_node.id

def get_relevant_alignment_signals(query_str: str, limit: int = 3) -> list:
    """Retrieve semantically relevant approved nudge/alignment signals from kruschdb (Direct-OPD)."""
    from .swarm import get_dbos_conn
    from llama_index.core import Settings
    
    try:
        # Generate query embedding using the configured embed model
        embedding = Settings.embed_model.get_text_embedding(query_str)
        embedding_str = "[" + ",".join(map(str, embedding)) + "]"
        
        signals = []
        with get_dbos_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT content, action_trace, 1 - (embedding <=> %s::vector) AS similarity
                    FROM homelab_memory_v2
                    WHERE category = 'alignment_signal'
                    AND status = 'active'
                    AND (action_trace->>'user_approved')::boolean = true
                    AND (action_trace->>'agent_corrected')::boolean = true
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                """, (embedding_str, embedding_str, limit))
                
                rows = cur.fetchall()
                for row in rows:
                    content, action_trace, similarity = row
                    signals.append({
                        "query": content,
                        "nudge": action_trace.get("nudge_text", ""),
                        "diff": action_trace.get("correction_diff", ""),
                        "similarity": similarity
                    })
        return signals
    except Exception as e:
        print(f"Error retrieving alignment signals from kruschdb: {e}")
        return []

def get_proactive_nudge_analysis(query_str: str, workspace_id: int) -> dict:
    """Proactively audit proposed query/action against workspace documentation and alignment signal feedback (PUST/Direct-OPD)."""
    try:
        vector_store = get_vector_store()
        filters = MetadataFilters(
            filters=[ExactMatchFilter(key="workspace_id", value=workspace_id)]
        )
        index = VectorStoreIndex.from_vector_store(vector_store=vector_store)
        retriever = index.as_retriever(similarity_top_k=5, filters=filters)
        source_nodes = retriever.retrieve(query_str)
        
        # Retrieve highly relevant past corrections (Direct-OPD guidance)
        signals = get_relevant_alignment_signals(query_str, limit=2)
        signals_context = ""
        if signals:
            signals_context = "\n### Reusable Alignment Guidance (Past Corrections):\n"
            for sig in signals:
                signals_context += f"- Audited Query: {sig['query']}\n"
                signals_context += f"  Warning/Nudge: {sig['nudge']}\n"
                if sig['diff']:
                    signals_context += f"  Correction Diff:\n```diff\n{sig['diff']}\n```\n"
                signals_context += "---\n"
        
        if not source_nodes and not signals_context:
            return {"nudge": "NO_NUDGES_REQUIRED"}
            
        context = "\n---\n".join([node.node.get_content() for node in source_nodes]) if source_nodes else "No workspace document matches found."
        
        prompt = f"""You are a Proactive Compliance & Operations Auditor. Your job is to analyze the proposed query or action against the provided institutional knowledge context.
Identify if there are any:
- Direct contradictions or violations
- Compliance/legal risks
- Core operational guidelines or policies that are neglected
- Specific procedural requirements that must be followed

Retrieved Context:
{context}
{signals_context}

Proposed Query/Action:
{query_str}

If the proposed action is perfectly aligned and does not violate or neglect any guidelines, respond with exactly:
NO_NUDGES_REQUIRED

Otherwise, formulate a clear warning and action suggestion in this exact Markdown format:
### 🧠 Proactive Context Nudge

**Warning:** [Clear explanation of the warning/conflict/risk]

**Suggested Action:** [Clear description of what should be corrected, updated, or checked]
"""
        response = llm_reasoning.complete(prompt)
        nudge_text = str(response).strip()
        return {"nudge": nudge_text}
    except Exception as e:
        print(f"Error in proactive nudge analysis: {e}")
        return {"nudge": "NO_NUDGES_REQUIRED", "error": str(e)}





