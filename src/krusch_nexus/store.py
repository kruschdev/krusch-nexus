"""
KruschNexus Minimal Storage Layer (store.py)
=============================================
Single source of truth for relational metadata, structural chunks,
pgvector embeddings, persistent embedding cache, and the 8-state ingest ledger.
Strictly enforces workspace isolation.
"""

import os
import json
from datetime import datetime, timezone
from typing import Optional, Generator, List, Dict, Any
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    Float,
    String,
    Boolean,
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

try:
    from sqlalchemy.dialects.postgresql import TSVECTOR
except ImportError:
    TSVECTOR = None

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
    mime = Column(String(100), default="application/octet-stream")
    detected_mime = Column(String(100), default="application/octet-stream")
    parser_name = Column(String(100), default="default")
    parser_version = Column(String(50), default="1.0")
    chunker_version = Column(String(50), default="1.0")
    total_pages = Column(Integer, default=1)
    total_chunks = Column(Integer, default=0)
    version = Column(Integer, default=1, server_default='1', index=True)
    ingest_report = Column(Text, nullable=True)  # JSON-encoded IngestReport
    ingested_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    ocr_pages = Column(Text, nullable=True)  # JSON list of page numbers where OCR was applied
    ocr_confidence = Column(Text, nullable=True)  # JSON dict {page_num: conf}
    status = Column(String(50), default="completed", index=True)
    original_path = Column(String(1024), nullable=True)
    mtime = Column(Float, nullable=True)
    embedding_model = Column(String(100), default="bge-large")
    embedding_dim = Column(Integer, default=1024)
    extra = Column(Text, nullable=True)  # JSON-encoded payload
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    workspace = relationship("Workspace", back_populates="documents")
    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")


class DocumentChunk(Base):
    """Relational chunk record preserving 1-based page number or locator, headings, and vectors."""
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "source_hash", name="uq_document_chunk_hash"),
        Index("ix_chunks_workspace_doc_type", "workspace_id", "doc_type"),
        Index("ix_chunks_doc_chunk_idx", "document_id", "chunk_index"),
        Index("ix_chunks_doc_page", "document_id", "page_number"),
    )

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(512), nullable=True, index=True)
    chunk_index = Column(Integer, nullable=False, default=0, index=True)
    page_number = Column(Integer, nullable=True, index=True)  # None for unpaged docs like DOCX/CSV
    locator = Column(String(255), nullable=True)             # Structural breadcrumb or row range
    header = Column(String(255), nullable=True)
    heading_path = Column(Text, nullable=True)               # JSON-encoded list of heading hierarchy strings
    content = Column(Text, nullable=False)                   # Raw chunk text (used for embedding & search)
    citation = Column(String(512), nullable=True)            # Formatted citation
    source_hash = Column(String(64), nullable=False, index=True)
    doc_hash = Column(String(64), nullable=False, index=True)
    doc_type = Column(String(50), default="general", index=True)
    chunker_version = Column(String(50), default="1.0")
    embed_model = Column(String(100), default="bge-large")
    confidence = Column(Float, nullable=True)
    char_start = Column(Integer, nullable=True)
    char_end = Column(Integer, nullable=True)
    bbox = Column(Text, nullable=True)  # JSON-encoded [left, top, width, height]
    is_superseded = Column(Boolean, default=False, server_default='false', index=True)
    tsv_content = Column(Text().with_variant(TSVECTOR, "postgresql"), nullable=True) if TSVECTOR is not None else Column(Text, nullable=True)
    embedding = Column(Vector(1024), nullable=True) if Vector else Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    document = relationship("Document", back_populates="chunks")


class IngestRun(Base):
    """Persistent 8-state machine run tracking every ingestion attempt."""
    __tablename__ = "ingest_runs"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True)
    workspace = Column(String(255), nullable=True, index=True)
    workspace_name = Column(String(255), nullable=True)
    filename = Column(String(512), nullable=False)
    file_hash = Column(String(64), nullable=False, index=True)
    state = Column(String(50), nullable=False, default="detected", index=True)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)
    duration_ms = Column(Float, default=0.0)
    error_class = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)
    payload = Column(Text, nullable=True)  # JSON-encoded IngestReport
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# Legacy alias for backward compatibility
IngestReportRecord = IngestRun


class EmbedCache(Base):
    """Persistent on-disk / database embedding cache preventing re-embedding on daemon restart."""
    __tablename__ = "embed_cache"
    __table_args__ = (
        UniqueConstraint("text_hash", "model", name="uq_embed_cache_hash_model"),
        Index("ix_embed_cache_hash_model", "text_hash", "model"),
    )

    id = Column(Integer, primary_key=True, index=True)
    text_hash = Column(String(64), nullable=False, index=True)
    model = Column(String(100), nullable=False, default="bge-large", index=True)
    dim = Column(Integer, default=1024)
    vector = Column(Text, nullable=False)  # JSON-serialized float list
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class SearchTrace(Base):
    """
    Explainability fuse trace for hybrid retrieval scoring and ranking.
    Zero document text logged; purely structural hashes and rank mappings for offline tuning.
    """
    __tablename__ = "search_traces"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    query_hash = Column(String(64), nullable=False, index=True)
    dense_ranks = Column(Text, nullable=True)     # JSON-encoded mapping {chunk_id: rank}
    sparse_ranks = Column(Text, nullable=True)    # JSON-encoded mapping {chunk_id: rank}
    fused_ranks = Column(Text, nullable=True)     # JSON-encoded mapping {chunk_id: rank}
    boosts_applied = Column(Text, nullable=True)  # JSON-encoded mapping {chunk_id: [boost_names]}
    duration_ms = Column(Float, default=0.0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class OperatorAudit(Base):
    """Audit ledger for sensitive/destructive operator actions (delete, reparse)."""
    __tablename__ = "operator_audits"

    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(50), nullable=False, index=True)  # "delete", "reparse"
    document_id = Column(Integer, nullable=True, index=True)
    workspace_id = Column(Integer, nullable=True, index=True)
    confirmation_token = Column(String(100), nullable=False)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    details = Column(Text, nullable=True)  # JSON-encoded details


# ─── Database Engine & Session Management ─────────────────────────────────────

_ENGINES: Dict[str, Any] = {}


def get_engine(db_url: Optional[str] = None):
    url = db_url or os.getenv("DATABASE_URL")
    if not url:
        url = "sqlite:///./nexus.db"

    if url in _ENGINES:
        return _ENGINES[url]

    if "kruschpassword" in url:
        raise ConfigurationError(
            "Refusing database connection with insecure default password 'kruschpassword'. "
            "Set POSTGRES_PASSWORD in your environment or compose file."
        )

    if "sqlite" in url:
        if ":memory:" in url or url == "sqlite://":
            from sqlalchemy.pool import StaticPool
            eng = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        else:
            eng = create_engine(url, connect_args={"check_same_thread": False})
    else:
        eng = create_engine(url)

    _ENGINES[url] = eng
    return eng


def get_session_factory(engine=None):
    eng = engine or get_engine()
    return sessionmaker(autocommit=False, autoflush=False, bind=eng)


from contextlib import contextmanager

@contextmanager
def get_db_session(engine=None, workspace_id: Optional[int] = None):
    factory = get_session_factory(engine)
    session = factory()
    try:
        if workspace_id is not None:
            bind = session.get_bind()
            if bind is not None and getattr(bind, "dialect", None) and bind.dialect.name == "postgresql":
                session.execute(text("SET LOCAL app.current_workspace_id = :ws_id"), {"ws_id": str(workspace_id)})
        yield session
    finally:
        session.close()


def init_db(engine=None):
    """
    Initialize relational schema for SQLite or local dev environments.
    Production PostgreSQL environments use Alembic migrations.
    """
    target_engine = engine or get_engine()
    Base.metadata.create_all(bind=target_engine)
