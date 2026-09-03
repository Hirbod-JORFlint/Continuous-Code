#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Post-Edit Diagnostics Hook (Python port).

Port of hooks/src/post-edit-diagnostics.ts:
  Runs shift-left diagnostics after file edits.
  Queries TLDR daemon for type errors and lint issues immediately after Edit/Write.
  Provides early feedback before tests run.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import daemon_client  # noqa: E402
from _payload import read_stdin, tool_input, tool_name  # noqa: E402

# Code file extensions we care about
CODE_EXTENSIONS = [
    # Python (has linters: pyright + ruff)
    ".py", ".pyx", ".pyi",
    # TypeScript/JavaScript (TODO: add eslint/tsc)
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    # Go (TODO: add go vet)
    ".go",
    # Rust (TODO: add clippy)
    ".rs",
    # Java
    ".java",
    # C/C++
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".hh",
    # Ruby
    ".rb",
    # C#
    ".cs",
]

# Currently only Python has linters configured in tldr diagnostics
PYTHON_EXTENSIONS = [".py", ".pyx", ".pyi"]


def main() -> None:
    payload = read_stdin(timeout=2.0)

    # Only run on Edit and Write operations
    name = tool_name(payload)
    if name != "Edit" and name != "Write":
        print("{}")
        return

    params = tool_input(payload)
    file_path = params.get("file_path")
    if not file_path:
        print("{}")
        return

    # Skip non-code files entirely
    last_dot = file_path.rfind(".")
    ext = file_path[last_dot:] if last_dot >= 0 else ""
    if ext not in CODE_EXTENSIONS:
        print("{}")
        return

    # Skip other languages until we add their linters
    if ext not in PYTHON_EXTENSIONS:
        print("{}")
        return

    # Query daemon for diagnostics
    try:
        project_dir = os.environ.get("OPC_PROJECT_DIR") or os.getcwd()
        response = daemon_client.query_daemon_sync(
            {"cmd": "diagnostics", "file": file_path},
            project_dir,
        )

        # If daemon is unavailable or no errors, silently succeed
        if response.get("status") == "unavailable" or response.get("error"):
            print("{}")
            return

        # Handle both direct response and summary-wrapped response formats
        summary = response.get("summary") or response
        type_errors = summary.get("type_errors") or 0
        lint_issues = summary.get("lint_errors") or summary.get("lint_issues") or 0
        errors = response.get("errors") or []

        # Track hook activity (P8) - reuse projectDir from above
        daemon_client.track_hook_activity_sync(
            "post-edit-diagnostics", project_dir, True,
            {"edits_analyzed": 1, "type_errors": type_errors, "lint_issues": lint_issues},
        )

        # No errors - silent success
        if type_errors == 0 and lint_issues == 0:
            print("{}")
            return

        # Build error summary
        lines: list[str] = []
        lines.append(f"⚠️ Diagnostics: {type_errors} type errors, {lint_issues} lint issues")

        # Show up to 5 error previews
        max_previews = 5
        previews = errors[:max_previews]

        for err in previews:
            location = f"{err.get('file')}:{err.get('line')}:{err.get('column')}" if err.get("column") else f"{err.get('file')}:{err.get('line')}"
            lines.append(f"   - {location}: {err.get('message')}")

        # Show "... and N more" if there are more errors
        if len(errors) > max_previews:
            remaining = len(errors) - max_previews
            lines.append(f"   ... and {remaining} more")

        output: dict[str, Any] = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": "\n".join(lines),
            }
        }
        print(json.dumps(output))
    except Exception:
        # Daemon error - silently ignore (graceful degradation)
        print("{}")


if __name__ == "__main__":
    main()
