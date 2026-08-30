#!/usr/bin/env python3
"""Forwarding shim for the canonical neutral hook launcher.

Re-executes harness/lifecycle/hooks/hook_launcher.py so that legacy Claude
Code settings.json hook commands ("uv run $HOME/.claude/hooks/hook_launcher.py
<name>") keep resolving after the launcher was re-homed into the harness
tree. Step 11 removes this shim together with the rest of `.claude/`.

Resolution order:
1. $OPC_HARNESS_DIR/lifecycle/hooks
2. $OPC_PROJECT_DIR/harness/lifecycle/hooks
3. ~/.opc/hooks  (install-root junction -> harness/lifecycle/hooks)
4. $HOME/harness/lifecycle/hooks  (repo-root fallback)
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def find_canonical_launcher() -> Path | None:
    """Locate the canonical neutral hook launcher."""
    candidates: list[Path] = []
    harness_dir = os.environ.get("OPC_HARNESS_DIR")
    if harness_dir:
        candidates.append(Path(harness_dir) / "lifecycle" / "hooks" / "hook_launcher.py")
    project_dir = os.environ.get("OPC_PROJECT_DIR")
    if project_dir:
        candidates.append(
            Path(project_dir) / "harness" / "lifecycle" / "hooks" / "hook_launcher.py"
        )
    candidates.append(Path.home() / ".opc" / "hooks" / "hook_launcher.py")
    candidates.append(Path.home() / "harness" / "lifecycle" / "hooks" / "hook_launcher.py")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def main() -> None:
    launcher = find_canonical_launcher()
    if launcher is None:
        print("Error: canonical neutral hook launcher not found", file=sys.stderr)
        sys.exit(1)
    sys.argv = [str(launcher), *sys.argv[1:]]
    runpy.run_path(str(launcher), run_name="__main__")


if __name__ == "__main__":
    main()
