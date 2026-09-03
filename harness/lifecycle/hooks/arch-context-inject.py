#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""PreToolUse Hook - Architecture Context Injection (Python port).

Port of hooks/src/arch-context-inject.ts:
  When a Task prompt mentions planning, design, or architecture keywords,
  this hook runs `tldr arch` and injects the architectural layer information
  into the prompt.

This gives agents/subagents architectural context for better planning.
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
from _payload import read_stdin, tool_input, tool_name  # noqa: E402

# Planning-related keywords that trigger arch injection
PLANNING_PATTERNS = [
    re.compile(r"\bplan\b", re.IGNORECASE),
    re.compile(r"\bdesign\b", re.IGNORECASE),
    re.compile(r"\barchitecture\b", re.IGNORECASE),
    re.compile(r"\brefactor\b", re.IGNORECASE),
    re.compile(r"\brestructure\b", re.IGNORECASE),
    re.compile(r"\breorganize\b", re.IGNORECASE),
    re.compile(r"\bmodularize\b", re.IGNORECASE),
    re.compile(r"\bsplit\s+(?:into|up)\b", re.IGNORECASE),
    re.compile(r"\bextract\s+(?:to|into)\b", re.IGNORECASE),
    re.compile(r"\bcreate\s+(?:new\s+)?(?:module|package|service)\b", re.IGNORECASE),
]


def has_planning_intent(text: str) -> bool:
    return any(pattern.search(text) for pattern in PLANNING_PATTERNS)


def get_architecture(project_path: str) -> dict[str, Any] | None:
    try:
        response = daemon_client.query_daemon_sync({"cmd": "arch", "language": "python"}, project_path)

        # Handle daemon unavailable or errors
        if response.get("status") == "unavailable" or response.get("status") == "error":
            return None

        # Handle indexing state
        if response.get("indexing"):
            return None

        # Parse result from daemon
        result = response.get("result")
        if not result:
            return None

        layers: dict[str, list[str]] = {}

        # Entry layer functions
        entry_layer = result.get("entry_layer")
        if entry_layer and isinstance(entry_layer, list):
            layers["entry"] = [f"{f.get('file')}:{f.get('function')}" for f in entry_layer[:15]]

        # Leaf layer functions
        leaf_layer = result.get("leaf_layer")
        if leaf_layer and isinstance(leaf_layer, list):
            layers["leaf"] = [f"{f.get('file')}:{f.get('function')}" for f in leaf_layer[:15]]

        # Circular dependencies
        circular = None
        cdeps = result.get("circular_dependencies")
        if cdeps:
            circular = [f"{c.get('a')} <-> {c.get('b')}" for c in cdeps]

        if len(layers) == 0:
            return None

        return {"layers": layers, "circular": circular}
    except Exception:
        return None


def format_arch_context(arch: dict[str, Any]) -> str:
    lines: list[str] = ["## Architecture Layers"]

    for layer, files in arch["layers"].items():
        if not files or len(files) == 0:
            continue

        lines.append("")
        lines.append(f"### {layer.upper()}")
        for file in files[:10]:
            lines.append(f"- {file}")
        if len(files) > 10:
            lines.append(f"- ... and {len(files) - 10} more")

    if arch.get("circular") and len(arch["circular"]) > 0:
        lines.append("")
        lines.append("### Circular Dependencies (WARNING)")
        for dep in arch["circular"][:5]:
            lines.append(f"- {dep}")

    return "\n".join(lines)


def main() -> None:
    try:
        payload = read_stdin(timeout=2.0)

        # Only intercept Task tool
        if tool_name(payload) != "Task":
            print("{}")
            return

        params = tool_input(payload)
        prompt = params.get("prompt") or ""
        description = params.get("description") or ""
        full_text = f"{prompt} {description}"

        # Skip if no planning intent detected
        if not has_planning_intent(full_text):
            print("{}")
            return

        # Skip if already has architecture context
        if "## Architecture" in prompt or "### ENTRY" in prompt or "### SERVICE" in prompt:
            print("{}")
            return

        # Get project path
        project_dir = os.environ.get("OPC_PROJECT_DIR") or payload_project_dir(payload, default=os.getcwd())

        if not project_dir or not Path(project_dir).exists():
            print("{}")
            return

        # Get architecture
        arch = get_architecture(project_dir)

        if not arch:
            print("{}")
            return

        # Format and inject context
        arch_context = format_arch_context(arch)

        enhanced_prompt = f"{arch_context}\n\n---\n\n{prompt}"

        output: dict[str, Any] = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": "Injected architecture context for planning task",
                "updatedInput": {
                    **params,
                    "prompt": enhanced_prompt,
                },
            },
        }

        # Track hook activity for flush threshold
        daemon_client.track_hook_activity_sync(
            "arch-context-inject", project_dir, True,
            {"tasks_processed": 1, "arch_injected": 1},
        )

        print(json.dumps(output))
    except Exception:
        # Silent fail - don't block task execution
        print("{}")


if __name__ == "__main__":
    main()
