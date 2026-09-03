#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Pre-Edit Context Injection Hook (Python port).

Port of hooks/src/edit-context-inject.ts:
  Injects file structure from TLDR before Claude edits a file.
  Uses TLDR daemon for fast code extraction (replaces CLI spawning).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import daemon_client  # noqa: E402
from _payload import read_stdin, session_id, tool_input, tool_name  # noqa: E402


def get_project_dir() -> str:
    return os.environ.get("OPC_PROJECT_DIR") or os.getcwd()


def get_tldr_imports(file_path: str) -> list[dict[str, Any]]:
    try:
        response = daemon_client.query_daemon_sync(
            {"cmd": "imports", "file": file_path},
            get_project_dir(),
        )

        if response.get("indexing") or response.get("status") == "unavailable" or response.get("status") == "error":
            return []

        if response.get("imports") and isinstance(response.get("imports"), list):
            return response["imports"]

        return []
    except Exception:
        return []


def get_tldr_extract(file_path: str, session_id: str | None = None) -> dict[str, Any] | None:
    try:
        response = daemon_client.query_daemon_sync(
            {"cmd": "extract", "file": file_path, "session": session_id},
            get_project_dir(),
        )

        # Skip if daemon is indexing or unavailable
        if response.get("indexing") or response.get("status") == "unavailable" or response.get("status") == "error":
            return None

        if response.get("result"):
            return response["result"]

        return None
    except Exception:
        return None


def main() -> None:
    try:
        payload = read_stdin(timeout=2.0)

        if tool_name(payload) != "Edit":
            print("{}")
            return

        params = tool_input(payload)
        file_path = params.get("file_path")
        if not file_path:
            print("{}")
            return

        # Get file structure from TLDR (pass session_id for token tracking)
        extract = get_tldr_extract(file_path, session_id(payload))
        imports = get_tldr_imports(file_path)

        class_count = len((extract or {}).get("classes") or []) if extract else 0
        func_count = len((extract or {}).get("functions") or []) if extract else 0
        import_count = len(imports)
        total = class_count + func_count

        if total == 0 and import_count == 0:
            print("{}")
            return

        # Build compact context message
        parts: list[str] = []

        # Show imports first - important for understanding dependencies
        if import_count > 0:
            import_modules = [i.get("module") for i in imports[:8]]
            parts.append(f"Dependencies: {', '.join(import_modules)}{'...' if import_count > 8 else ''}")

        if class_count > 0 and extract:
            class_names = [c.get("name") for c in extract.get("classes", [])][:10]
            parts.append(f"Classes: {', '.join(class_names)}{'...' if class_count > 10 else ''}")

        if func_count > 0 and extract:
            # Show function names with param counts for quick reference
            func_summaries = []
            for f in extract.get("functions", [])[:12]:
                param_count = len(f.get("params") or [])
                func_summaries.append(f"{f.get('name')}({param_count})" if param_count > 0 else f.get("name"))
            parts.append(f"Functions: {', '.join(func_summaries)}{'...' if func_count > 12 else ''}")

        symbol_info = f"{total} symbols" if total > 0 else ""
        dep_info = f"{import_count} deps" if import_count > 0 else ""
        summary = ", ".join(x for x in [symbol_info, dep_info] if x)

        output: dict[str, Any] = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": f"[Edit context: {os.path.basename(file_path)} - {summary}]\n{'\n'.join(parts)}",
            }
        }

        # Track hook activity for flush threshold
        project_dir = get_project_dir()
        daemon_client.track_hook_activity_sync(
            "edit-context-inject", project_dir, True,
            {"edits_processed": 1, "symbols_shown": total},
        )

        print(json.dumps(output))
    except Exception:
        print("{}")


if __name__ == "__main__":
    main()
