"""
Backward-compatibility shim for src.backend.ingest_daemon.
Deprecated: Use 'from krusch_nexus.ingest import IngestPipeline, auto_ingest_loop, main' instead.
"""
from krusch_nexus.ingest import (
    IngestPipeline,
    IngestStateMachine,
    auto_ingest_loop,
    main,
    classify_document_type
)

__all__ = [
    "IngestPipeline",
    "IngestStateMachine",
    "auto_ingest_loop",
    "main",
    "classify_document_type"
]

if __name__ == "__main__":
    main()
