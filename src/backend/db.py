import os
from dotenv import load_dotenv

# Ensure environment variables from .env are loaded
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"))
load_dotenv()

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, text, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime, timezone
try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    Vector = None

# Flexible Database URL resolution: Local kruschdb vs. Polygres Cloud
DB_TARGET = os.getenv("NEXUS_DB_TARGET", "local").lower()
POLYGRES_URL = os.getenv("POLYGRES_URL")
LOCAL_DB_URL = os.getenv("DATABASE_URL", "postgresql://kdcode:password@localhost:5432/krusch_nexus_db")

if DB_TARGET == "polygres" and POLYGRES_URL:
    DATABASE_URL = POLYGRES_URL
    # Ensure SSL is enabled for cloud PostgreSQL connection
    engine = create_engine(DATABASE_URL, connect_args={"sslmode": "require"})
else:
    DATABASE_URL = LOCAL_DB_URL
    if "sqlite" in DATABASE_URL:
        engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
    else:
        engine = create_engine(DATABASE_URL)

def is_polygres_backend():
    return DB_TARGET == "polygres" or "polygres" in DATABASE_URL
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Workspace(Base):
    __tablename__ = "workspaces"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    description = Column(Text, nullable=True)
    subscription_tier = Column(String, default="free")
    doc_limit = Column(Integer, default=5000)
    query_limit_monthly = Column(Integer, default=50000)
    queries_used = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    documents = relationship("Document", back_populates="workspace")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if getattr(self, "subscription_tier", None) is None:
            self.subscription_tier = "free"
        if getattr(self, "doc_limit", None) is None:
            self.doc_limit = 5000
        if getattr(self, "query_limit_monthly", None) is None:
            self.query_limit_monthly = 50000
        if getattr(self, "queries_used", None) is None:
            self.queries_used = 0

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    role = Column(String, default="user")
    subscription_tier = Column(String, default="free")
    api_key = Column(String, nullable=True, index=True)
    license_key = Column(String, nullable=True, index=True)
    email_verified = Column(Boolean, default=False)
    verification_token = Column(String, nullable=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class Document(Base):
    __tablename__ = "documents"
    
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"))
    file_hash = Column(String(64), index=True, nullable=True) # SHA-256 for deduplication
    total_pages = Column(Integer, default=1)
    total_chunks = Column(Integer, default=0)
    doc_type = Column(String(50), default="general", index=True) # 'authority', 'work_product', 'fact_narrative', 'general'
    ingest_report = Column(Text, nullable=True) # JSON summary of ingestion metrics
    allowed_roles = Column(String, default="all")   # 'all', or comma-separated roles e.g. 'admin,management,user'
    classification_level = Column(String, default="internal") # 'public', 'internal', 'confidential', 'management_only'
    flagged_for_review = Column(Boolean, default=False)
    flag_reason = Column(Text, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    
    workspace = relationship("Workspace", back_populates="documents")
    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")

class DocumentChunk(Base):
    """Relational chunk record preserving page-level provenance, headings, and hashes."""
    __tablename__ = "document_chunks"
    
    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), index=True)
    filename = Column(String, index=True)
    page_number = Column(Integer, default=1, index=True)
    chunk_index = Column(Integer, default=0, index=True)
    header = Column(String(255), nullable=True)
    content = Column(Text, nullable=False)
    source_hash = Column(String(64), index=True, nullable=True)
    doc_hash = Column(String(64), index=True, nullable=True)
    doc_type = Column(String(50), default="general", index=True)
    embedding = Column(Vector(1024), nullable=True) if Vector else Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    document = relationship("Document", back_populates="chunks")

class GraphNode(Base):
    __tablename__ = "graph_nodes"
    
    id = Column(Integer, primary_key=True, index=True)
    entity_name = Column(String, index=True)
    entity_type = Column(String, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    status = Column(String, default="active")       # active, stale, resolved
    custom_metadata = Column(Text, nullable=True)   # JSON string for extra metadata/provenance
    created_at = Column(DateTime, default=datetime.utcnow)

class GraphEdge(Base):
    __tablename__ = "graph_edges"
    
    id = Column(Integer, primary_key=True, index=True)
    source_node_id = Column(Integer, ForeignKey("graph_nodes.id"))
    target_node_id = Column(Integer, ForeignKey("graph_nodes.id"))
    relationship_type = Column(String, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    status = Column(String, default="active")       # active, stale, resolved
    created_at = Column(DateTime, default=datetime.utcnow)
    
    source_node = relationship("GraphNode", foreign_keys=[source_node_id])
    target_node = relationship("GraphNode", foreign_keys=[target_node_id])

class Employee(Base):
    __tablename__ = "employees"
    
    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String, index=True)          # 'google', 'microsoft', 'local'
    external_id = Column(String, index=True)       # ID in source system
    email = Column(String, unique=True, index=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    display_name = Column(String, nullable=True)
    job_title = Column(String, nullable=True)
    department = Column(String, nullable=True)
    status = Column(String, default="active")       # active, suspended, deleted
    custom_metadata = Column(Text, nullable=True)   # JSON string for extra metadata
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    # 1. Create standard SQLAlchemy relational tables
    Base.metadata.create_all(bind=engine)

    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS license_key VARCHAR;"))
            conn.commit()
        except Exception as e:
            pass

    # 2. Initialize pgvector extension, HNSW vector index, vector store, and SQL helper procedures
    if engine.dialect.name == "postgresql":
        with engine.connect() as conn:
            try:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
                conn.commit()
            except Exception as e:
                print(f"pgvector extension init: {e}")
                
            try:
                conn.execute(text("""
                CREATE TABLE IF NOT EXISTS data_workspace_documents_vectors_bge (
                    id SERIAL PRIMARY KEY,
                    text TEXT,
                    metadata_ JSONB,
                    node_id VARCHAR,
                    embedding vector(1024)
                );
                """))
                conn.commit()
            except Exception as e:
                print(f"Vector table init: {e}")

            try:
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_workspace_docs_bge_hnsw ON data_workspace_documents_vectors_bge USING hnsw (embedding vector_cosine_ops);"))
                conn.commit()
            except Exception as e:
                print(f"HNSW Index setup: {e}")

            try:
                conn.execute(text("""
                CREATE OR REPLACE FUNCTION nexus_hybrid_search(
                    query_vector vector(1024),
                    target_workspace_id INT DEFAULT NULL,
                    top_k INT DEFAULT 10
                )
                RETURNS TABLE (
                    id INT,
                    text TEXT,
                    metadata_ jsonb,
                    similarity FLOAT
                ) AS $$
                BEGIN
                    RETURN QUERY
                    SELECT 
                        v.id,
                        v.text,
                        v.metadata_,
                        (1 - (v.embedding <=> query_vector))::FLOAT AS similarity
                    FROM data_workspace_documents_vectors_bge v
                    WHERE (target_workspace_id IS NULL OR (v.metadata_->>'workspace_id')::INT = target_workspace_id)
                    ORDER BY v.embedding <=> query_vector ASC
                    LIMIT top_k;
                END;
                $$ LANGUAGE plpgsql;
                """))
                conn.commit()
            except Exception as e:
                print(f"nexus_hybrid_search procedure init: {e}")

            try:
                conn.execute(text("""
                CREATE OR REPLACE FUNCTION nexus_graph_walk(
                    start_node_ids INT[],
                    target_workspace_id INT DEFAULT NULL,
                    max_depth INT DEFAULT 2
                )
                RETURNS TABLE (
                    edge_id INT,
                    source_name VARCHAR,
                    relationship_type VARCHAR,
                    target_name VARCHAR,
                    hop_depth INT
                ) AS $$
                BEGIN
                    RETURN QUERY
                    WITH RECURSIVE graph_walk AS (
                        SELECT 
                            e.id as edge_id,
                            e.source_node_id,
                            e.target_node_id,
                            e.relationship_type,
                            1 as hop_depth
                        FROM graph_edges e
                        WHERE e.source_node_id = ANY(start_node_ids)
                          AND (target_workspace_id IS NULL OR e.workspace_id = target_workspace_id)

                        UNION ALL

                        SELECT 
                            e.id as edge_id,
                            e.source_node_id,
                            e.target_node_id,
                            e.relationship_type,
                            gw.hop_depth + 1
                        FROM graph_edges e
                        INNER JOIN graph_walk gw ON e.source_node_id = gw.target_node_id
                        WHERE gw.hop_depth < max_depth
                          AND (target_workspace_id IS NULL OR e.workspace_id = target_workspace_id)
                    )
                    SELECT 
                        gw.edge_id,
                        sn.entity_name as source_name,
                        gw.relationship_type,
                        tn.entity_name as target_name,
                        gw.hop_depth
                    FROM graph_walk gw
                    JOIN graph_nodes sn ON gw.source_node_id = sn.id
                    JOIN graph_nodes tn ON gw.target_node_id = tn.id
                    ORDER BY gw.hop_depth ASC;
                END;
                $$ LANGUAGE plpgsql;
                """))
                conn.commit()
            except Exception as e:
                print(f"nexus_graph_walk procedure init: {e}")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ─── Polygres Native C-Extension & Recursive CTE Optimization Helpers ───

def polygres_graph_walk(db, start_node_ids: list[int], max_hops: int = 2, workspace_ids: list[int] = None):
    """
    Executes a high-performance PostgreSQL recursive CTE query (or pgGraph)
    offloading multi-hop graph walks directly to the database kernel.
    """
    if not start_node_ids:
        return []
    
    ws_filter = ""
    if workspace_ids:
        ws_ids_str = ",".join(str(i) for i in workspace_ids)
        ws_filter = f"AND e.workspace_id IN ({ws_ids_str})"

    start_ids_str = ",".join(str(i) for i in start_node_ids)

    query_str = f"""
    WITH RECURSIVE graph_walk AS (
        SELECT 
            e.id as edge_id,
            e.source_node_id,
            e.target_node_id,
            e.relationship_type,
            e.workspace_id,
            1 as hop_depth
        FROM graph_edges e
        WHERE e.source_node_id IN ({start_ids_str}) {ws_filter}

        UNION ALL

        SELECT 
            e.id as edge_id,
            e.source_node_id,
            e.target_node_id,
            e.relationship_type,
            e.workspace_id,
            gw.hop_depth + 1
        FROM graph_edges e
        INNER JOIN graph_walk gw ON e.source_node_id = gw.target_node_id
        WHERE gw.hop_depth < {max_hops} {ws_filter}
    )
    SELECT 
        gw.edge_id,
        sn.entity_name as source_name,
        gw.relationship_type,
        tn.entity_name as target_name,
        gw.workspace_id,
        gw.hop_depth
    FROM graph_walk gw
    JOIN graph_nodes sn ON gw.source_node_id = sn.id
    JOIN graph_nodes tn ON gw.target_node_id = tn.id
    ORDER BY gw.hop_depth ASC;
    """

    try:
        result = db.execute(text(query_str))
        rows = result.fetchall()
        return [
            {
                "edge_id": row.edge_id,
                "source": row.source_name,
                "relationship": row.relationship_type,
                "target": row.target_name,
                "workspace_id": row.workspace_id,
                "hop_depth": row.hop_depth
            }
            for row in rows
        ]
    except Exception as e:
        # Fallback for non-Postgres (SQLite) or missing tables
        return []

def polygres_vector_search(db, embedding: list[float], top_k: int = 10, table_name: str = "data_vector_store"):
    """
    Executes native HNSW vector distance queries (<-> / pgvector / pgContext)
    directly against PostgreSQL for maximum retrieval throughput.
    """
    if not embedding:
        return []
        
    vec_str = "[" + ",".join(str(f) for f in embedding) + "]"
    query_str = f"""
    SELECT id, text, metadata_, 1 - (embedding <=> '{vec_str}'::vector) as similarity
    FROM {table_name}
    ORDER BY embedding <=> '{vec_str}'::vector ASC
    LIMIT {top_k};
    """
    try:
        result = db.execute(text(query_str))
        rows = result.fetchall()
        return [
            {
                "id": row.id,
                "text": row.text,
                "metadata": row.metadata_,
                "similarity": float(row.similarity)
            }
            for row in rows
        ]
    except Exception:
        return []

