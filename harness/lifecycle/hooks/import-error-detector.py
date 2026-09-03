#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Import Error Detector - PostToolUse hook that detects Python import errors
and suggests the /dependency-preflight skill (Python port).

Port of .claude/hooks/src/import-error-detector.ts:
Runs on Bash tool output, matches patterns like:
- ModuleNotFoundError: No module named 'X'
- ImportError: cannot import name 'Y'
- No module named 'Z'

Returns a system reminder suggesting the skill when errors are detected.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _payload import read_stdin, tool_name  # noqa: E402


# Error patterns to detect
IMPORT_ERROR_PATTERNS = [
    re.compile(r"ModuleNotFoundError:\s*No module named\s*['\"]?(\w+)['\"]?", re.IGNORECASE),
    re.compile(r"ImportError:\s*cannot import name\s*['\"]?(\w+)['\"]?", re.IGNORECASE),
    re.compile(r"ImportError:\s*No module named\s*['\"]?(\w+)['\"]?", re.IGNORECASE),
    re.compile(r"No module named\s*['\"]?(\w+)['\"]?", re.IGNORECASE),
    re.compile(r"ModuleNotFoundError", re.IGNORECASE),
    re.compile(r"circular import", re.IGNORECASE),
]


def detect_import_error(output: str) -> dict[str, Any]:
    for pattern in IMPORT_ERROR_PATTERNS:
        match = pattern.search(output)
        if match:
            return {
                "detected": True,
                "module": match.group(1) if match.lastindex else None,
            }
    return {"detected": False}


def main() -> None:
    payload = read_stdin(timeout=2.0)

    # Only process Bash tool output
    if tool_name(payload) != "Bash":
        print(json.dumps({"result": "continue"}))
        return

    # Check both output and error fields
    parts = [payload.get("tool_output"), payload.get("error")]
    text_to_check = "\n".join(str(p) for p in parts if p)

    if not text_to_check:
        print(json.dumps({"result": "continue"}))
        return

    result = detect_import_error(text_to_check)

    if result.get("detected"):
        module_name = f" (module: {result['module']})" if result.get("module") else ""
        module_or_placeholder = result.get("module") or "<module>"
        output: dict[str, Any] = {
            "result": "continue",
            "message": (
                f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔧 IMPORT ERROR DETECTED{module_name}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"\n"
                f"Consider using /dependency-preflight skill to diagnose:\n"
                f"\n"
                f"1. Check Python version: uv run python --version\n"
                f"2. Check if installed: uv pip show {module_or_placeholder}\n"
                f"3. Verify import: uv run python -c \"import {module_or_placeholder}\"\n"
                f"\n"
                f"Or invoke the skill: /dependency-preflight\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ),
        }
        print(json.dumps(output))
    else:
        print(json.dumps({"result": "continue"}))


if __name__ == "__main__":
    main()
