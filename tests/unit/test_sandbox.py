"""
Unit tests for KruschNexus Path Sandboxing.
Verifies rejection of directory traversal, system paths (/etc/passwd),
and enforcement of allowed ingest roots.
"""

import os
import tempfile
import unittest
from pathlib import Path

from krusch_nexus.ingest import validate_safe_path
from krusch_nexus.exceptions import PathSandboxError


class TestPathSandbox(unittest.TestCase):

    def test_prohibited_system_paths_rejected(self):
        """Verify direct attempts to access /etc or /proc raise PathSandboxError."""
        with self.assertRaises(PathSandboxError) as ctx:
            validate_safe_path("/etc/passwd")
        self.assertIn("prohibited system directory", str(ctx.exception))

        with self.assertRaises(PathSandboxError):
            validate_safe_path("/etc/shadow")

    def test_relative_traversal_to_system_paths_rejected(self):
        """Verify ../../ traversal targeting /etc is resolved and rejected."""
        fake_relative = os.path.join(tempfile.gettempdir(), "..", "..", "etc", "passwd")
        with self.assertRaises(PathSandboxError):
            validate_safe_path(fake_relative)

    def test_allowed_roots_enforcement(self):
        """Verify paths outside configured allowed_roots are rejected."""
        with tempfile.TemporaryDirectory() as allowed_dir:
            with tempfile.TemporaryDirectory() as foreign_dir:
                # File inside allowed root
                good_file = os.path.join(allowed_dir, "contract.pdf")
                with open(good_file, "w") as f:
                    f.write("test")

                # File outside allowed root
                bad_file = os.path.join(foreign_dir, "leak.pdf")
                with open(bad_file, "w") as f:
                    f.write("test")

                # Should succeed for good_file
                resolved_good = validate_safe_path(
                    good_file,
                    allowed_roots=[allowed_dir],
                    allow_temp_dirs=False
                )
                self.assertEqual(resolved_good, Path(good_file).resolve())

                # Should fail for bad_file
                with self.assertRaises(PathSandboxError) as ctx:
                    validate_safe_path(
                        bad_file,
                        allowed_roots=[allowed_dir],
                        allow_temp_dirs=False
                    )
                self.assertIn("Path sandbox violation", str(ctx.exception))

    def test_symlink_escape_outside_allowed_roots_rejected(self):
        """Verify symlinks placed inside allowed root pointing outside are resolved and rejected."""
        with tempfile.TemporaryDirectory() as allowed_dir:
            with tempfile.TemporaryDirectory() as foreign_dir:
                secret_file = os.path.join(foreign_dir, "confidential.txt")
                with open(secret_file, "w") as f:
                    f.write("outside secret")

                # Create symlink inside allowed_dir pointing to foreign secret_file
                symlink_path = os.path.join(allowed_dir, "symlink_leak.txt")
                os.symlink(secret_file, symlink_path)

                with self.assertRaises(PathSandboxError) as ctx:
                    validate_safe_path(
                        symlink_path,
                        allowed_roots=[allowed_dir],
                        allow_temp_dirs=False
                    )
                self.assertIn("Path sandbox violation", str(ctx.exception))

    def test_symlink_to_prohibited_system_file_rejected(self):
        """Verify symlink pointing to /etc/passwd is caught and rejected."""
        with tempfile.TemporaryDirectory() as allowed_dir:
            symlink_path = os.path.join(allowed_dir, "passwd_link")
            os.symlink("/etc/passwd", symlink_path)

            with self.assertRaises(PathSandboxError) as ctx:
                validate_safe_path(
                    symlink_path,
                    allowed_roots=[allowed_dir],
                    allow_temp_dirs=False
                )
            self.assertIn("prohibited system directory", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
