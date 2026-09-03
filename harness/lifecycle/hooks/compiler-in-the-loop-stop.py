#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Compiler-in-the-Loop Stop Hook (Python port).

Port of .claude/hooks/src/compiler-in-the-loop-stop.ts (re-homed .claude -> .opc):
Prevents Claude from stopping if there are unresolved Lean errors/sorries.
Implements the APOLLO recursive repair pattern.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _payload import read_stdin  # noqa: E402


opc_dir = os.environ.get("OPC_PROJECT_DIR")
STATE_DIR = str(Path(opc_dir) / ".opc" / "cache" / "lean") if opc_dir else str(Path(tempfile.gettempdir()) / "claude-lean")

STATE_FILE = str(Path(STATE_DIR) / "compiler-state.json")

# Max age for state (5 minutes) - ignore stale state
MAX_STATE_AGE_MS = 5 * 60 * 1000


def load_state() -> dict[str, Any] | None:
    if not Path(STATE_FILE).exists():
        return None

    try:
        state: dict[str, Any] = json.loads(Path(STATE_FILE).read_text(encoding="utf-8"))

        # Check if state is stale
        if (time.time() * 1000) - state.get("timestamp", 0) > MAX_STATE_AGE_MS:
            Path(STATE_FILE).unlink(missing_ok=True)
            return None

        return state
    except Exception:
        return None


def clear_state() -> None:
    if Path(STATE_FILE).exists():
        Path(STATE_FILE).unlink(missing_ok=True)


def main() -> None:
    payload = read_stdin(timeout=2.0)

    # CRITICAL: Prevent infinite loops
    if payload.get("stop_hook_active"):
        print("{}")
        return

    state = load_state()

    # No Lean state or no errors - allow stop
    if not state or not state.get("has_errors"):
        print("{}")
        return

    # Check if state is for current session
    if state.get("session_id") != payload.get("session_id"):
        clear_state()
        print("{}")
        return

    # Build repair prompt based on error type
    sorries = state.get("sorries") or []
    file_path = state.get("file_path", "")
    errors = state.get("errors", "")

    if len(sorries) > 0:
        repair_prompt = (
            f"\n🔄 APOLLO REPAIR LOOP - Unresolved 'sorry' placeholders\n"
            f"\n"
            f"File: {file_path}\n"
            f"\n"
            f"The proof has {len(sorries)} incomplete part(s):\n"
            f"\n"
            f"{chr(10).join(sorries)}\n"
            f"\n"
            f"**Your task:**\n"
            f"1. Pick ONE sorry to fix (start with the simplest)\n"
            f"2. Replace 'sorry' with a valid proof:\n"
            f"   - Try tactics: simp, ring, nlinarith, norm_num, exact, apply\n"
            f"   - Or provide explicit proof term\n"
            f"3. Re-run to check if it compiles\n"
            f"\n"
            f"Continue fixing until all sorries are resolved.\n"
        )
    else:
        repair_prompt = (
            f"\n🔄 APOLLO REPAIR LOOP - Lean Compiler Errors\n"
            f"\n"
            f"File: {file_path}\n"
            f"\n"
            f"Errors:\n"
            f"{str(errors)[:2000]}\n"
            f"\n"
            f"**Your task:**\n"
            f"1. Read the error messages carefully\n"
            f"2. If type error: check signatures match\n"
            f"3. If syntax error: check Lean 4 syntax\n"
            f"4. If unknown identifier: check imports\n"
            f"5. Consider using 'sorry' to isolate the failing part, then fix incrementally\n"
            f"\n"
            f"Fix the errors and re-write the file.\n"
        )

    # Block stop, inject repair prompt
    print(json.dumps({
        "decision": "block",
        "reason": repair_prompt,
    }))


if __name__ == "__main__":
    main()
