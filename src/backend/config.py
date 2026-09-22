"""
Backward-compatibility shim for src.backend.config.
Deprecated: Use 'from krusch_nexus.config import NexusConfig' instead.
"""
from krusch_nexus.config import NexusConfig

__all__ = ["NexusConfig"]
