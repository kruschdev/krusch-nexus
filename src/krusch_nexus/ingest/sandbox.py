"""
KruschNexus Ingestion Sandbox & Path Security (sandbox.py)
==========================================================
Path validation, traversal guards, root enforcement, and stale lock reaping.
"""

import os
import time
import json
import shutil
import logging
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Union

from ..exceptions import PathSandboxError, UnsupportedMimeError

logger = logging.getLogger("krusch_nexus.ingest.sandbox")

ALLOWED_EXT = (
    '.pdf', '.docx', '.doc', '.eml', '.msg', '.txt',
    '.md', '.csv', '.json', '.html', '.htm',
    '.py', '.js', '.ts', '.yaml', '.yml', '.sql'
)

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
    Guards against path traversal, symlink escapes, and system file ingestion.
    """
    raw_p = Path(file_path)
    is_link = os.path.islink(file_path) or raw_p.is_symlink()

    try:
        resolved = Path(file_path).resolve()
    except Exception as e:
        raise PathSandboxError(f"Invalid path specification '{file_path}': {e}")

    # 1. Deny forbidden system paths
    for denied in DENIED_SYSTEM_ROOTS:
        try:
            if resolved == denied or resolved.is_relative_to(denied):
                raise PathSandboxError(
                    f"Path sandbox violation: Access denied: Path '{resolved}' falls within prohibited system directory '{denied}'"
                )
        except AttributeError:
            try:
                resolved.relative_to(denied)
                raise PathSandboxError(
                    f"Path sandbox violation: Access denied: Path '{resolved}' falls within prohibited system directory '{denied}'"
                )
            except ValueError:
                pass

    # 2. Enforce allowed roots
    valid_roots: List[Path] = []
    if allowed_roots:
        for r in allowed_roots:
            if r:
                valid_roots.append(Path(r).resolve())
    else:
        valid_roots.append(Path.cwd().resolve())

    if allow_temp_dirs:
        valid_roots.append(Path(tempfile.gettempdir()).resolve())
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
                f"Path sandbox violation: Access denied: Path '{resolved}' escapes allowed ingestion roots [{roots_str}]"
            )

    # 3. Strictly reject symlinks (no symlink follow)
    if is_link:
        raise PathSandboxError(
            f"Path sandbox violation: Symlinks are disallowed (no symlink follow): '{file_path}' is a symbolic link."
        )

    return resolved


def reap_stale_locks(
    staging_dir: str,
    timeout_seconds: float = 600.0,
    max_age_seconds: Optional[float] = None
) -> List[str]:
    """
    Scan staging directory for abandoned .part and .lock files older than timeout.
    Quarantines or unlinks them to prevent deadlocks.
    Returns list of reaped file paths.
    """
    timeout = max_age_seconds if max_age_seconds is not None else timeout_seconds
    reaped = []
    now = time.time()
    if not os.path.exists(staging_dir):
        return reaped

    for root, _, files in os.walk(staging_dir):
        for f in files:
            if f.endswith(".part") or f.endswith(".lock"):
                f_path = os.path.join(root, f)
                try:
                    mtime = os.path.getmtime(f_path)
                    if now - mtime > timeout:
                        if f.endswith(".lock"):
                            try:
                                os.unlink(f_path)
                            except OSError:
                                pass
                            reaped.append(f_path)
                            logger.info(f"Reaped stale lock file: {f_path}")
                        else:
                            ws_name = os.path.basename(root)
                            base_dir = Path(staging_dir).parent
                            failed_dir = base_dir / ".failed" / ws_name
                            failed_dir.mkdir(parents=True, exist_ok=True)
                            clean_name = f.replace(".part", "")
                            dest = failed_dir / f"stale_{clean_name}"
                            shutil.move(f_path, str(dest))
                            sidecar = failed_dir / f"stale_{clean_name}.error.json"
                            with open(sidecar, "w", encoding="utf-8") as s_file:
                                json.dump({
                                    "filename": clean_name,
                                    "workspace": ws_name,
                                    "error_class": "StaleLockReaped",
                                    "error_message": f"Lock age ({int(now - mtime)}s) exceeded timeout ({int(timeout)}s)",
                                    "timestamp": datetime.now(timezone.utc).isoformat()
                                }, s_file, indent=2)
                            reaped.append(str(dest))
                            logger.warning(f"Reaped stale lock: {f_path} -> {dest}")
                except Exception as e:
                    logger.debug(f"Error checking potential stale lock {f_path}: {e}")
    return reaped


def sanitize_filename(filename: str) -> str:
    """Strip traversal tokens and hazardous characters from filename."""
    base = os.path.basename(filename)
    return "".join(c for c in base if c.isalnum() or c in "._- ")


def validate_file_magic_bytes(file_path: Union[str, Path]) -> None:
    """
    Validate file header magic bytes prior to spooling or parsing.
    Rejects executable payloads (PE, ELF, Mach-O) and polyglot files disguised as documents.
    """
    p = Path(file_path)
    if not p.is_file() or p.stat().st_size == 0:
        return

    ext = p.suffix.lower()
    with open(p, "rb") as f:
        header = f.read(512)

    # 1. Deny executable binaries across all extensions
    if header.startswith(b"MZ"):
        raise UnsupportedMimeError(f"Security Rejection: Windows PE executable binary detected: '{p.name}'")
    if header.startswith(b"\x7fELF"):
        raise UnsupportedMimeError(f"Security Rejection: Linux ELF executable binary detected: '{p.name}'")
    if header[:4] in (b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe"):
        raise UnsupportedMimeError(f"Security Rejection: Mach-O executable binary detected: '{p.name}'")

    # 2. Deny HTML or script masquerading as PDF
    if ext == ".pdf":
        header_lower = header.lower()
        if (
            b"<html" in header_lower
            or b"<!doctype" in header_lower
            or b"<script" in header_lower
            or b"<?php" in header_lower
        ):
            raise UnsupportedMimeError(f"Security Rejection: Script/HTML masquerading as PDF: '{p.name}'")
        if b"%PDF-" not in header[:1024]:
            raise UnsupportedMimeError(f"Format Rejection: Invalid or corrupted PDF header: '{p.name}'")

