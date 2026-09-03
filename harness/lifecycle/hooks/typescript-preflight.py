#!/usr/bin/env python3
"""PostToolUse hook: TypeScript pre-flight check (neutral Python port, Step 11 Phase B).

Runs the typescript_check.py script after Edit/Write on .ts/.tsx files to catch
errors immediately; returns errors as a block reason so the agent can fix them.

The check script location is re-homed to the harness tree (opc/scripts/...)
instead of the legacy .claude/scripts/... fallback.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from _payload import read_stdin, project_dir


def _script_locations(project_root: Path) -> list[Path]:
    candidates = [
        project_root / "opc" / "scripts" / "core" / "typescript_check.py",
        project_root / "opc" / "scripts" / "typescript_check.py",
        project_root / "scripts" / "typescript_check.py",
    ]
    if sys.platform == "win32":
        # legacy fallback on user profile (kept for compatibility)
        import os
        home = os.environ.get("USERPROFILE")
        if home:
            candidates.append(Path(home) / ".opc" / "scripts" / "typescript_check.py")
    return candidates


def main() -> None:
    payload = read_stdin()
    tool_name = payload.get("tool_name") or payload.get("toolName") or ""

    if tool_name not in ("Edit", "Write"):
        print("{}")
        return

    response = payload.get("tool_response") or {}
    if not isinstance(response, dict):
        response = {}
    file_path = response.get("filePath") or response.get("file_path")
    tool_input = payload.get("tool_input") or {}
    if isinstance(tool_input, dict):
        file_path = file_path or tool_input.get("file_path")
    if not file_path or not isinstance(file_path, str):
        print("{}")
        return

    if not (file_path.endswith(".ts") or file_path.endswith(".tsx")):
        print("{}")
        return

    if "node_modules" in file_path or ".test." in file_path:
        print("{}")
        return

    project_root = Path(project_dir(payload, default=str(Path.cwd())))
    script_path = next((p for p in _script_locations(project_root) if p.exists()), None)
    if script_path is None:
        print("{}")
        return

    try:
        result = subprocess.run(
            [sys.executable, str(script_path), "--file", file_path, "--json"],
            capture_output=True,
            text=True,
            timeout=35.0,
        )
        check_result = json.loads(result.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError):
        print("{}")
        return

    if not check_result.get("has_errors"):
        print("{}")
        return

    lines = [f"TypeScript Pre-flight Check: {check_result.get('summary', '')}", ""]
    tsc_errors = check_result.get("tsc_errors") or []
    if tsc_errors:
        lines.append("**Type Errors:**")
        for err in tsc_errors[:5]:
            lines.append(f"  {err}")
    qlty_errors = check_result.get("qlty_errors") or []
    if qlty_errors:
        lines.append("**Lint Issues:**")
        for err in qlty_errors[:5]:
            lines.append(f"  {err}")
    lines.append("")
    lines.append("Fix these errors before proceeding.")

    print(json.dumps({"decision": "block", "reason": "\n".join(lines)}))


if __name__ == "__main__":
    main()
