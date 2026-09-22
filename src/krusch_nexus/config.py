"""
KruschNexus Configuration & Air-Gap Enforcement
================================================
Encapsulates runtime parameters, security bounds, path allowlists,
and compile/startup air-gap guarantees.
"""

import os
from typing import Optional, List
from pydantic import BaseModel, Field, model_validator

from .exceptions import AirGapViolationError, ConfigurationError


class NexusConfig(BaseModel):
    """
    Immutable configuration object ensuring strict air-gapped guarantees,
    isolated workspaces, and safe path bounds.
    """
    database_url: str = Field(
        default_factory=lambda: os.getenv("DATABASE_URL", "")
    )
    embedding_provider: str = Field(
        default_factory=lambda: os.getenv("EMBEDDING_PROVIDER", "ollama").lower()
    )
    allow_cloud: bool = Field(
        default_factory=lambda: os.getenv("ALLOW_CLOUD", "0") in ("1", "true", "True")
    )
    ollama_url: str = Field(
        default_factory=lambda: os.getenv(
            "OLLAMA_EMBED_HOST",
            os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
        )
    )
    embed_model: str = Field(
        default_factory=lambda: os.getenv("OLLAMA_EMBED_MODEL", "bge-large")
    )
    embed_batch_size: int = 16
    embed_timeout: float = 45.0
    subprocess_timeout: float = 30.0
    ocr_threshold_chars: int = 30
    ocr_dpi: int = 150
    max_chars_per_chunk: int = 2000
    overlap_chars: int = 150
    max_file_size_bytes: int = 52_428_800  # 50 MB
    max_page_count: int = 500              # Cap on multi-page processing
    max_embed_queue_depth: int = 500       # Max pending embeddings
    watch_dir: Optional[str] = None
    allowed_ingest_roots: List[str] = Field(default_factory=list)
    max_ocr_workers: int = 2
    max_embed_workers: int = 4

    @model_validator(mode="after")
    def validate_security_and_airgap(self) -> "NexusConfig":
        # 1. Enforce air-gap: EMBEDDING_PROVIDER must be ollama unless ALLOW_CLOUD=1
        if self.embedding_provider != "ollama" and not self.allow_cloud:
            raise AirGapViolationError(
                f"Insecure embedding provider '{self.embedding_provider}' rejected. "
                "KruschNexus requires local 'ollama' to guarantee an air-gapped corpus. "
                "Set ALLOW_CLOUD=1 and install cloud extras if external egress is explicitly intended."
            )

        # 2. Reject default insecure credentials (kruschpassword)
        if "kruschpassword" in self.database_url:
            raise ConfigurationError(
                "Refusing startup: Insecure default database password 'kruschpassword' detected. "
                "Provide a secure, explicit POSTGRES_PASSWORD in your environment / DATABASE_URL."
            )

        # 3. Fallback database URL for unconfigured dev/test
        if not self.database_url:
            # Check if running in test mode or local default
            if os.getenv("TESTING", "0") in ("1", "true") or os.getenv("PYTEST_CURRENT_TEST"):
                self.database_url = "sqlite:///./nexus_test.db"
            else:
                self.database_url = "sqlite:///./nexus.db"

        # 4. Standardize watch_dir into allowed roots if provided
        if self.watch_dir and self.watch_dir not in self.allowed_ingest_roots:
            self.allowed_ingest_roots.append(os.path.abspath(self.watch_dir))

        return self

    @classmethod
    def from_env(cls, **overrides) -> "NexusConfig":
        """Instantiate configuration from environment variables with optional overrides."""
        return cls(**overrides)
