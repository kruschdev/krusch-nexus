"""
KruschNexus Minimal Storage Layer (store.py)
=============================================
Single source of truth for relational metadata, structural chunks,
and pgvector embeddings. Strictly enforces workspace isolation.
DDL migrations are managed via Alembic.
"""

import os
import json
from datetime import datetime, timezone
from typing import Optional, Generator, List
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    Float,
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
    mime = Column(String(100), default="application/octet-stream")
    parser_version = Column(String(50), default="1.0")
    chunker_version = Column(String(50), default="1.0")
    total_pages = Column(Integer, default=1)
    total_chunks = Column(Integer, default=0)
    ingest_report = Column(Text, nullable=True)  # JSON-encoded IngestReport
    ingested_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    ocr_pages = Column(Text, nullable=True)  # JSON list of page numbers where OCR was applied
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
    embedding = Column(Vector(1024), nullable=True) if Vector else Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    document = relationship("Document", back_populates="chunks")


class IngestReportRecord(Base):
    """Persistent ledger of every ingestion attempt, duration, and error."""
    __tablename__ = "ingest_reports"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True)
    filename = Column(String(512), nullable=False)
    file_hash = Column(String(64), nullable=False, index=True)
    status = Column(String(50), nullable=False, default="completed", index=True)
    duration_ms = Column(Float, default=0.0)
    error_class = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)
    payload = Column(Text, nullable=True)  # JSON-encoded details
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


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
    Initialize relational schema for SQLite or local dev environments.
    Production PostgreSQL environments use Alembic migrations.
    """
    target_engine = engine or get_engine()
    Base.metadata.create_all(bind=target_engine)
