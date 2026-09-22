"""
Backward-compatibility shim for src.backend.main.
Deprecated: Use 'from krusch_nexus.api import app' instead.
"""
from krusch_nexus.api import app

__all__ = ["app"]
