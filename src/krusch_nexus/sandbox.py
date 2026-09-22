"""
KruschNexus Path Sandboxing & Ingest Root Validation
===================================================
Guards against path traversal, symlink attacks, and arbitrary system file ingestion
(e.g., /etc/shadow, ~/.ssh/id_rsa) in privileged environments.
"""

import os
import tempfile
from pathlib import Path
from typing import List, Optional

from .exceptions import PathSandboxError

# Standard system locations strictly forbidden from ingestion
DENIED_SYSTEM_ROOTS = [
    Path("/etc").resolve(),
    Path("/proc").resolve(),
    Path("/sys").resolve(),
    Path("/dev").resolve(),
    Path("/var/run").resolve(),
    Path(os.path.expanduser("~/.ssh")).resolve(),
    Path(os.path.expanduser("~/.gnupg")).resolve(),
]


def validate_safe_path(
    file_path: str,
    allowed_roots: Optional[List[str]] = None,
    allow_temp_dirs: bool = True
) -> Path:
    """
    Resolve and validate that a target file path is within safe ingest bounds.
    
    Args:
        file_path: Relative or absolute path to validate.
        allowed_roots: List of approved directory roots. If provided, file must reside inside one.
        allow_temp_dirs: Whether to permit temporary directories (for programmatic uploads).
        
    Returns:
        Fully resolved Path object.
        
    Raises:
        PathSandboxError: If path violates sandbox rules or targets sensitive system files.
    """
    try:
        resolved = Path(file_path).resolve()
    except Exception as e:
        raise PathSandboxError(f"Invalid path specification '{file_path}': {e}")

    # 1. Deny forbidden system paths
    for denied in DENIED_SYSTEM_ROOTS:
        try:
            if resolved == denied or resolved.is_relative_to(denied):
                raise PathSandboxError(
                    f"Access denied: Path '{resolved}' falls within prohibited system directory '{denied}'"
                )
        except AttributeError:
            # Fallback for Python < 3.9 (is_relative_to)
            try:
                resolved.relative_to(denied)
                raise PathSandboxError(
                    f"Access denied: Path '{resolved}' falls within prohibited system directory '{denied}'"
                )
            except ValueError:
                pass

    # 2. If explicit allowed_roots are configured, enforce strict inclusion
    valid_roots: List[Path] = []
    if allowed_roots:
        for r in allowed_roots:
            if r:
                valid_roots.append(Path(r).resolve())
    else:
        valid_roots.append(Path.cwd().resolve())

    if allow_temp_dirs:
        valid_roots.append(Path(tempfile.gettempdir()).resolve())
        # Also allow /tmp explicitly on Linux
        if os.path.exists("/tmp"):
            valid_roots.append(Path("/tmp").resolve())

    if valid_roots:
        is_safe = False
        for root in valid_roots:
            try:
                if resolved == root or resolved.is_relative_to(root):
                    is_safe = True
                    break
            except AttributeError:
                try:
                    resolved.relative_to(root)
                    is_safe = True
                    break
                except ValueError:
                    pass

        if not is_safe:
            roots_str = ", ".join(str(r) for r in valid_roots)
            raise PathSandboxError(
                f"Path sandbox violation: '{resolved}' is not within any approved ingest root: [{roots_str}]"
            )

    return resolved
