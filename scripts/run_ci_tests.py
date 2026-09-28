#!/usr/bin/env python3
"""
scripts/run_ci_tests.py
=======================
CI test execution wrapper for GitHub Actions.
Executes pytest with streaming stdout/stderr, captures failures,
emits GitHub Actions ::error annotations for check-run visibility,
and appends structured failure diagnostics to $GITHUB_STEP_SUMMARY.
"""

import os
import re
import sys
import subprocess


def main():
    args = sys.argv[1:]
    if not args:
        args = ["tests/unit/", "-v"]

    cmd = [sys.executable, "-m", "pytest"] + args
    print(f"[run_ci_tests] Executing: {' '.join(cmd)}")
    sys.stdout.flush()

    # Run subprocess and stream output live while capturing
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    captured_lines = []
    for line in iter(proc.stdout.readline, ''):
        sys.stdout.write(line)
        sys.stdout.flush()
        captured_lines.append(line)

    proc.stdout.close()
    return_code = proc.wait()

    if return_code != 0:
        print(f"\n[run_ci_tests] Pytest failed with exit code {return_code}", file=sys.stderr)
        
        # Extract failed test lines and summary
        failed_tests = []
        in_short_summary = False
        failures_section = []
        in_failures = False

        for raw_line in captured_lines:
            line = raw_line.strip()
            if "=== short test summary info ===" in line:
                in_short_summary = True
                continue
            if in_short_summary:
                if line.startswith("FAILED "):
                    failed_tests.append(line)
                elif line.startswith("ERROR "):
                    failed_tests.append(line)
                elif line.startswith("==="):
                    in_short_summary = False

            if "=== FAILURES ===" in line or "=== ERRORS ===" in line:
                in_failures = True
                continue
            if in_failures:
                if line.startswith("=== short test summary info ===") or (line.startswith("===") and "failed" in line):
                    in_failures = False
                else:
                    failures_section.append(line)

        # Emit GitHub Actions workflow commands (::error)
        if failed_tests:
            for ft in failed_tests:
                # Sanitize newlines for workflow commands
                clean_msg = ft.replace("\n", " ").replace("\r", "")
                print(f"::error title=Pytest Test Failure::{clean_msg}", file=sys.stderr)
        else:
            err_lines = [
                cline.strip()
                for cline in captured_lines
                if "Error:" in cline or "Exception:" in cline or "FAILED" in cline
            ]
            for el in err_lines[:5]:
                print(f"::error title=Pytest Execution Error::{el}", file=sys.stderr)

        # Write to $GITHUB_STEP_SUMMARY if present
        step_summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if step_summary_path:
            try:
                with open(step_summary_path, "a", encoding="utf-8") as f:
                    f.write(f"\n### ❌ Pytest Failure in `{' '.join(args)}` (Exit Code: {return_code})\n\n")
                    if failed_tests:
                        f.write("**Failed Tests:**\n")
                        for ft in failed_tests:
                            f.write(f"- `{ft}`\n")
                        f.write("\n")
                    if failures_section:
                        f.write("<details><summary>Failure Tracebacks</summary>\n\n```python\n")
                        f.write("\n".join(failures_section[:150]))
                        f.write("\n```\n</details>\n\n")
            except Exception as e:
                print(f"[run_ci_tests] Warning: Failed to write to GITHUB_STEP_SUMMARY: {e}", file=sys.stderr)

        sys.exit(return_code)

    print(f"\n[run_ci_tests] All tests passed successfully for: {' '.join(args)}")
    sys.exit(0)


if __name__ == "__main__":
    main()
