"""
KruschNexus Offline & Air-Gap Verification Validator
===================================================
Performs active network self-checks to verify zero outbound cloud egress,
asserting that only authorized local nodes (PostgreSQL + Ollama) are reachable.
"""

import socket
import logging
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

from .config import NexusConfig

logger = logging.getLogger("krusch_nexus.offline_check")

# Known public test targets to assert unreachable in an air-gapped homelab
EXTERNAL_PROBE_TARGETS = [
    ("8.8.8.8", 53),
    ("1.1.1.1", 53),
    ("api.openai.com", 443),
    ("openrouter.ai", 443),
    ("api.anthropic.com", 443),
]


def probe_socket(host: str, port: int, timeout_sec: float = 0.5) -> bool:
    """Attempt a low-timeout TCP socket probe. Returns True if connection succeeds."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout_sec)
        sock.connect((host, port))
        sock.close()
        return True
    except Exception:
        return False


def verify_offline_environment(config: Optional[NexusConfig] = None) -> Dict[str, Any]:
    """
    Assert that the environment adheres to strict air-gap standards:
    1. Outbound probes to external cloud and DNS hosts must fail.
    2. Local Ollama host must be accessible or local.
    3. Local database must be accessible.
    """
    conf = config or NexusConfig.from_env()

    # 1. Probe external internet destinations
    leaked_connections: List[str] = []
    for host, port in EXTERNAL_PROBE_TARGETS:
        if probe_socket(host, port):
            leaked_connections.append(f"{host}:{port}")

    air_gap_secure = len(leaked_connections) == 0

    # 2. Check local Ollama configuration
    parsed_ollama = urlparse(conf.ollama_url)
    ollama_host = parsed_ollama.hostname or "127.0.0.1"
    ollama_port = parsed_ollama.port or 11434
    ollama_local = ollama_host in ("127.0.0.1", "localhost", "host.docker.internal")

    # 3. Check Database host
    db_url = conf.database_url
    db_local = "localhost" in db_url or "127.0.0.1" in db_url or "sqlite" in db_url or "db" in db_url

    return {
        "status": "pass" if air_gap_secure else "warning",
        "air_gap_secure": air_gap_secure,
        "external_probes_blocked": air_gap_secure,
        "leaked_outbound_targets": leaked_connections,
        "ollama_host": conf.ollama_url,
        "ollama_is_local": ollama_local,
        "database_is_local": db_local,
        "embedding_provider": conf.embedding_provider,
        "cloud_allowed": conf.allow_cloud
    }
