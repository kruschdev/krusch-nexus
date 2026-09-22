"""
Backward-compatibility shim for src.backend.client.
Deprecated: Use 'from krusch_nexus import Nexus' instead.
"""
import warnings
warnings.warn("src.backend.client is deprecated; import from krusch_nexus instead", DeprecationWarning, stacklevel=2)

from krusch_nexus.client import Nexus, NexusIngestClient

__all__ = ["Nexus", "NexusIngestClient"]
