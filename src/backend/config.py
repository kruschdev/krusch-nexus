"""
KruschNexus Configuration Object
================================
Encapsulates all runtime configuration for database, embeddings,
OCR thresholds, chunking parameters, and worker concurrency.
Avoids reliance on ambient global os.environ variables.
"""

import os
from typing import Optional
from pydantic import BaseModel, Field


class NexusConfig(BaseModel):
    database_url: str = Field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL",
            "postgresql://krusch:kruschpassword@localhost:5432/krusch_nexus_db"
        )
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
    ocr_threshold_chars: int = 30
    ocr_dpi: int = 150
    max_chars_per_chunk: int = 2000
    overlap_chars: int = 150
    max_file_size_bytes: int = 52_428_800  # 50 MB
    watch_dir: Optional[str] = None
    max_ocr_workers: int = 2
    max_embed_workers: int = 4

    @classmethod
    def from_env(cls) -> "NexusConfig":
        return cls()
