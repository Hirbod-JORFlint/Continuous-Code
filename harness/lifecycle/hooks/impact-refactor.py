#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""UserPromptSubmit Hook - Impact Analysis for Refactoring (DAEMON) (Python port).

Port of hooks/src/impact-refactor.ts:
  When user mentions refactor/change/rename + function name, automatically
  runs impact analysis via daemon and injects the results as context.

Uses TLDR daemon for fast cached responses (50ms vs 500ms CLI).
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
from _payload import project_dir as payload_project_dir  # noqa: E402
from _payload import prompt_text, read_stdin  # noqa: E402

# Keywords that trigger impact analysis
REFACTOR_KEYWORDS = [
    re.compile(r"\brefactor\b", re.IGNORECASE),
    re.compile(r"\brename\b", re.IGNORECASE),
    re.compile(r"\bchange\b.*\bfunction\b", re.IGNORECASE),
    re.compile(r"\bmodify\b.*\b(?:function|method|class)\b", re.IGNORECASE),
    re.compile(r"\bupdate\b.*\bsignature\b", re.IGNORECASE),
    re.compile(r"\bmove\b.*\bfunction\b", re.IGNORECASE),
    re.compile(r"\bdelete\b.*\b(?:function|method)\b", re.IGNORECASE),
    re.compile(r"\bremove\b.*\b(?:function|method)\b", re.IGNORECASE),
    re.compile(r"\bextract\b.*\b(?:function|method)\b", re.IGNORECASE),
    re.compile(r"\binline\b.*\b(?:function|method)\b", re.IGNORECASE),
]

# Extract function/method names from prompt
FUNCTION_PATTERNS = [
    re.compile(r"(?:refactor|rename|change|modify|update|move|delete|remove)\s+(?:the\s+)?(?:function\s+)?[`\"']?(\w+)[`\"']?", re.IGNORECASE),
    re.compile(r"[`\"'](\w+)[`\"']\s+(?:function|method)", re.IGNORECASE),
    re.compile(r"(?:function|method|def|fn)\s+[`\"']?(\w+)[`\"']?", re.IGNORECASE),
]

EXCLUDE_WORDS = {
    "the", "this", "that", "function", "method", "class", "file",
    "to", "from", "into", "a", "an", "and", "or", "for", "with",
}


def should_trigger(prompt: str) -> bool:
    return any(pattern.search(prompt) for pattern in REFACTOR_KEYWORDS)


def extract_function_names(prompt: str) -> list[str]:
    candidates: set[str] = set()

    for pattern in FUNCTION_PATTERNS:
        for match in pattern.finditer(prompt):
            name = match.group(1)
            if name and len(name) > 2 and name.lower() not in EXCLUDE_WORDS:
                candidates.add(name)

    # Also look for snake_case and camelCase identifiers
    identifier_pattern = re.compile(r"\b([a-z][a-z0-9_]*[a-z0-9])\b", re.IGNORECASE)
    for match in identifier_pattern.finditer(prompt):
        name = match.group(1)
        if len(name) > 4 and name.lower() not in EXCLUDE_WORDS:
            # Only add if it looks like a function name (has underscore or is camelCase)
            if "_" in name or re.search(r"[a-z][A-Z]", name):
                candidates.add(name)

    return list(candidates)


def format_callers(callers: list[dict[str, Any]]) -> str:
    if len(callers) == 0:
        return "No callers found (function may be an entry point or unused)"

    lines = []
    for c in callers[:15]:
        loc = f"{c.get('file')}:{c.get('line')}" if c.get("line") else c.get("file")
        lines.append(f"  - {c.get('function') or 'unknown'} in {loc}")
    result = "\n".join(lines)
    if len(callers) > 15:
        result += f"\n  ... and {len(callers) - 15} more"
    return result


def format_importers(importers: list[dict[str, Any]]) -> str:
    if len(importers) == 0:
        return "No importers found"

    lines = []
    for i in importers[:10]:
        loc = f"{i.get('file')}:{i.get('line')}" if i.get("line") else i.get("file")
        lines.append(f"  - {loc}")
    result = "\n".join(lines)
    if len(importers) > 10:
        result += f"\n  ... and {len(importers) - 10} more"
    return result


def get_importers_from_daemon(module_name: str, project_dir: str) -> list[dict[str, Any]] | None:
    try:
        response = daemon_client.query_daemon_sync({"cmd": "importers", "module": module_name}, project_dir)

        if response.get("indexing"):
            return None

        if response.get("status") == "unavailable" or response.get("status") == "error":
            return None

        if response.get("importers") and isinstance(response.get("importers"), list):
            return response["importers"]

        return []
    except Exception:
        return None


def get_impact_from_daemon(function_name: str, project_dir: str) -> list[dict[str, Any]] | None:
    try:
        response = daemon_client.query_daemon_sync({"cmd": "impact", "func": function_name}, project_dir)

        if response.get("indexing"):
            return None

        if response.get("status") == "unavailable" or response.get("status") == "error":
            return None

        if response.get("callers") and isinstance(response.get("callers"), list):
            return response["callers"]

        return []
    except Exception:
        return None


def main() -> None:
    payload = read_stdin(timeout=2.0)
    prompt = prompt_text(payload)

    # Check if this looks like a refactoring request
    if not should_trigger(prompt):
        print("")
        return

    # Extract function names
    functions = extract_function_names(prompt)
    if len(functions) == 0:
        print("")
        return

    project_dir = os.environ.get("OPC_PROJECT_DIR") or payload_project_dir(payload, default=os.getcwd())

    results: list[str] = []

    for func_name in functions[:3]:  # Max 3 functions
        callers = get_impact_from_daemon(func_name, project_dir)

        if callers is None:
            # Daemon unavailable, skip
            continue

        # Also get module-level importers (broader impact)
        importers = get_importers_from_daemon(func_name, project_dir)

        impact = f"📊 **Impact: {func_name}**\nCallers:\n{format_callers(callers)}"

        if importers and len(importers) > 0:
            impact += f"\n\nModule importers:\n{format_importers(importers)}"

        results.append(impact)

    # Track hook activity (P8)
    total_callers = 0
    if len(results) > 0:
        total_callers = len(re.findall("Callers:", " ".join(results)))

    daemon_client.track_hook_activity_sync(
        "impact-refactor", project_dir, True,
        {"analyses_run": len(functions), "results_found": len(results)},
    )

    if len(results) > 0:
        print(f"\n⚠️ **REFACTORING IMPACT ANALYSIS**\n\n{'\n\n'.join(results)}\n\nConsider callers AND importers before making changes.\n")
    else:
        print("")


if __name__ == "__main__":
    main()
