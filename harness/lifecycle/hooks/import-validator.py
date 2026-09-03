#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Import Validator Hook - PostToolUse (Python port).

Port of hooks/src/import-validator.ts:
  After Write/Edit, checks if imports reference symbols that exist.
  Uses TLDR daemon for fast symbol lookup (replaces CLI spawning).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import daemon_client  # noqa: E402
from _payload import read_stdin, tool_input, tool_name  # noqa: E402


def tldr_search(pattern: str, project_dir: str = ".") -> list[dict[str, Any]]:
    try:
        response = daemon_client.query_daemon_sync(
            {"cmd": "search", "pattern": pattern}, project_dir
        )

        # Skip if daemon is indexing or unavailable
        if response.get("indexing") or response.get("status") == "unavailable" or response.get("status") == "error":
            return []

        if response.get("results"):
            return response["results"]

        return []
    except Exception:
        return []


def extract_python_imports(code: str) -> list[dict[str, Any]]:
    imports: list[dict[str, Any]] = []

    # from X import Y, Z
    from_import_re = re.compile(r"from\s+([\w.]+)\s+import\s+([^#\n]+)")
    for match in from_import_re.finditer(code):
        module = match.group(1)
        symbols = [s.split(" as ")[0].strip() for s in match.group(2).split(",")]
        symbols = [s for s in symbols if s and s != "*"]
        if len(symbols) > 0:
            imports.append({"module": module, "symbols": symbols})

    return imports


def check_symbol_exists(symbol: str) -> dict[str, Any]:
    project_dir = os.environ.get("OPC_PROJECT_DIR") or "."

    # Search for function or class definition
    func_results = tldr_search(f"def {symbol}", project_dir)
    if len(func_results) > 0:
        return {"exists": True, "location": f"{func_results[0].get('file')}:{func_results[0].get('line')}"}

    class_results = tldr_search(f"class {symbol}", project_dir)
    if len(class_results) > 0:
        return {"exists": True, "location": f"{class_results[0].get('file')}:{class_results[0].get('line')}"}

    return {"exists": False}


def main() -> None:
    try:
        payload = read_stdin(timeout=2.0)

        name = tool_name(payload)
        if name != "Write" and name != "Edit":
            print("{}")
            return

        # Get the code that was written/edited
        params = tool_input(payload)
        code = params.get("content") or params.get("new_string") or ""
        if not code:
            print("{}")
            return

        # Only check Python files for now
        file_path = params.get("file_path") or ""
        if not file_path.endswith(".py"):
            print("{}")
            return

        # Extract and validate imports
        imports = extract_python_imports(code)
        warnings: list[str] = []

        for imp in imports:
            for symbol in imp["symbols"]:
                check = check_symbol_exists(symbol)
                if check.get("exists") and check.get("location"):
                    # Check if import path matches actual location
                    expected_module = imp["module"].replace(".", "/")
                    if expected_module not in check["location"]:
                        actual_file = os.path.basename(check["location"].split(":")[0])
                        warnings.append(f"{symbol}: imported from {imp['module']} but defined in {actual_file}")

        if len(warnings) == 0:
            print("{}")
            return

        output: dict[str, Any] = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": f"[Import check]\n{'\n'.join(warnings)}",
            }
        }

        # Track hook activity for flush threshold
        project_dir = os.environ.get("OPC_PROJECT_DIR") or "."
        daemon_client.track_hook_activity_sync(
            "import-validator", project_dir, True,
            {"writes_validated": 1, "warnings_found": len(warnings)},
        )

        print(json.dumps(output))
    except Exception:
        print("{}")


if __name__ == "__main__":
    main()
