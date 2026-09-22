"""
Backward-compatibility shim for src.backend.mcp_server.
Deprecated: Use 'from krusch_nexus.mcp import mcp, main' instead.
"""
from krusch_nexus.mcp import (
    mcp,
    main,
    set_client,
    get_client
)

__all__ = [
    "mcp",
    "main",
    "set_client",
    "get_client"
]

if __name__ == "__main__":
    main()
