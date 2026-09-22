"""
KruschNexus Minimal Storage Layer
=================================
Single source of truth for relational metadata, structural chunks,
and pgvector embeddings. Strictly enforces workspace isolation.
No graph nodes, no agent OS state, no default insecure credentials.
"""

import os
import json
from datetime import datetime, timezone
from typing import Optional, Generator, List
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    DateTime,
    Text,
    ForeignKey,
    UniqueConstraint,
    Index,
    text
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session

from .exceptions import ConfigurationError

try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    Vector = None

Base = declarative_base()


class Workspace(Base):
    """Isolated document collection workspace."""
    __tablename__ = "workspaces"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, index=True, nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    documents = relationship("Document", back_populates="workspace", cascade="all, delete-orphan")


class Document(Base):
    """Document record preserving file-level provenance, OCR stats, and model metadata."""
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("workspace_id", "file_hash", name="uq_workspace_file_hash"),
        Index("ix_doc_workspace_hash", "workspace_id", "file_hash"),
    )

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(512), nullable=False, index=True)
    file_hash = Column(String(64), nullable=False, index=True)  # SHA-256
    doc_type = Column(String(50), default="general", index=True)
    total_pages = Column(Integer, default=1)
    total_chunks = Column(Integer, default=0)
    ingest_report = Column(Text, nullable=True)  # JSON-encoded IngestReport
    ingested_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    ocr_pages = Column(Text, nullable=True)  # JSON list of page numbers where OCR was applied
    status = Column(String(50), default="completed", index=True)
    embedding_model = Column(String(100), default="bge-large")
    embedding_dim = Column(Integer, default=1024)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    workspace = relationship("Workspace", back_populates="documents")
    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")


class DocumentChunk(Base):
    """Relational chunk record preserving 1-based page number, heading breadcrumbs, and vectors."""
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "source_hash", name="uq_document_chunk_hash"),
        Index("ix_chunks_workspace_doc_type", "workspace_id", "doc_type"),
        Index("ix_chunks_doc_chunk_idx", "document_id", "chunk_index"),
    )

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(512), nullable=True, index=True)
    page_number = Column(Integer, nullable=False, default=1, index=True)
    chunk_index = Column(Integer, nullable=False, default=0, index=True)
    header = Column(String(255), nullable=True)
    content = Column(Text, nullable=False)
    source_hash = Column(String(64), nullable=False, index=True)
    doc_hash = Column(String(64), nullable=False, index=True)
    doc_type = Column(String(50), default="general", index=True)
    embedding = Column(Vector(1024), nullable=True) if Vector else Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    document = relationship("Document", back_populates="chunks")


# ─── Database Engine & Session Management ─────────────────────────────────────

def get_engine(db_url: Optional[str] = None):
    url = db_url or os.getenv("DATABASE_URL")
    if not url:
        # Default to SQLite for test or unconfigured local use
        url = "sqlite:///./nexus.db"

    if "kruschpassword" in url:
        raise ConfigurationError(
            "Refusing database connection with insecure default password 'kruschpassword'. "
            "Set POSTGRES_PASSWORD in your environment or compose file."
        )

    if "sqlite" in url:
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url)


def get_session_factory(engine=None):
    eng = engine or get_engine()
    return sessionmaker(autocommit=False, autoflush=False, bind=eng)


def init_db(engine=None):
    """
    Initialize relational schema and optional pgvector/full-text indexes.
    Safe for both PostgreSQL and SQLite environments.
    """
    target_engine = engine or get_engine()

    # 1. Create tables
    Base.metadata.create_all(bind=target_engine)

    # 2. PostgreSQL specific vector extension & HNSW indexing
    if target_engine.dialect.name == "postgresql":
        with target_engine.connect() as conn:
            try:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
                conn.commit()
            except Exception as e:
                pass

            try:
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw "
                    "ON document_chunks USING hnsw (embedding vector_cosine_ops);"
                ))
                conn.commit()
            except Exception as e:
                pass

            # TSVector search index on content + header
            try:
                conn.execute(text("""
                    ALTER TABLE document_chunks 
                    ADD COLUMN IF NOT EXISTS tsv tsvector 
                    GENERATED ALWAYS AS (
                        to_tsvector('english', coalesce(header, '') || ' ' || content)
                    ) STORED;
                """))
                conn.commit()
            except Exception as e:
                pass

            try:
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_chunks_tsv_gin "
                    "ON document_chunks USING gin (tsv);"
                ))
                conn.commit()
            except Exception as e:
                pass
