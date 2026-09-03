#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Signature Helper Hook - PreToolUse:Edit (Python port).

Port of hooks/src/signature-helper.ts:
  When Claude edits code containing function calls, inject the function signatures.
  Uses TLDR daemon for fast function lookup (replaces CLI spawning).
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
from _payload import read_stdin, session_id, tool_input, tool_name  # noqa: E402

# Keywords and builtins to skip
SKIP_NAMES = {
    "if", "for", "while", "with", "except", "match", "case",
    "print", "len", "str", "int", "list", "dict", "set", "tuple",
    "range", "enumerate", "zip", "map", "filter", "sorted", "reversed",
    "type", "isinstance", "hasattr", "getattr", "setattr", "super",
    "open", "input", "any", "all", "min", "max", "sum", "abs",
    "require", "import", "export", "return", "const", "let", "var",
    "function", "async", "await", "new", "this", "class", "extends",
}


def extract_function_calls(code: str) -> list[str]:
    # Match function calls: name(
    call_re = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
    calls: set[str] = set()
    for match in call_re.finditer(code):
        name = match.group(1)
        if name not in SKIP_NAMES:
            calls.add(name)
    return list(calls)


def get_project_dir() -> str:
    return os.environ.get("OPC_PROJECT_DIR") or os.getcwd()


def find_function_file(func_name: str, project_dir: str) -> str | None:
    try:
        response = daemon_client.query_daemon_sync(
            {"cmd": "search", "pattern": f"def {func_name}"},
            project_dir,
        )

        # Skip if daemon is indexing or unavailable
        if response.get("indexing") or response.get("status") == "unavailable" or response.get("status") == "error":
            return None

        results = response.get("results")
        if results and len(results) > 0:
            return f"{project_dir}/{results[0].get('file')}"
    except Exception:
        # ignore
        pass
    return None


def get_signature_from_tldr(func_name: str, file_path: str, session_id_value: str | None = None) -> str | None:
    try:
        response = daemon_client.query_daemon_sync(
            {"cmd": "extract", "file": file_path, "session": session_id_value},
            get_project_dir(),
        )

        # Skip if daemon is indexing or unavailable
        if response.get("indexing") or response.get("status") == "unavailable" or response.get("status") == "error":
            return None

        extract = response.get("result")
        if not extract or not isinstance(extract, dict) or not extract.get("functions"):
            return None

        for func in extract.get("functions", []):
            name = func.get("name")
            if name == func_name or name == f"async {func_name}":
                return func.get("signature")
    except Exception:
        # ignore
        pass
    return None


def main() -> None:
    try:
        payload = read_stdin(timeout=2.0)

        if tool_name(payload) != "Edit":
            print("{}")
            return

        params = tool_input(payload)
        new_code = params.get("new_string") or ""
        if not new_code or len(new_code) < 10:
            print("{}")
            return

        # Find function calls in the new code
        calls = extract_function_calls(new_code)
        if len(calls) == 0:
            print("{}")
            return

        project_dir = get_project_dir()
        signatures: list[str] = []

        # Look up signatures for first 5 calls (limit for performance)
        for call in calls[:5]:
            file_path = find_function_file(call, project_dir)
            if file_path:
                sig = get_signature_from_tldr(call, file_path, session_id(payload))
                if sig:
                    signatures.append(sig)

        if len(signatures) == 0:
            print("{}")
            return

        output: dict[str, Any] = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": f"[Signatures from TLDR]\n{'\n'.join(signatures)}",
            }
        }

        # Track hook activity for flush threshold
        daemon_client.track_hook_activity_sync(
            "signature-helper", project_dir, True,
            {"edits_checked": 1, "signatures_found": len(signatures)},
        )

        print(json.dumps(output))
    except Exception:
        print("{}")


if __name__ == "__main__":
    main()
