#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Compiler-in-the-Loop Hook (Python port).

Port of .claude/hooks/src/compiler-in-the-loop.ts (re-homed .claude -> .opc):
PostToolUse handler for .lean files:
- Runs Lean compiler on written files
- Calls Goedel-Prover-V2-8B via LMStudio for tactic suggestions
- Stores errors in state file for Stop hook
- Provides compiler feedback + AI suggestions to Claude
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _payload import read_stdin, tool_input, tool_name  # noqa: E402


# LMStudio endpoint for Goedel-Prover-V2-8B
LMSTUDIO_BASE_URL = os.environ.get("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234")
LMSTUDIO_ENDPOINT = os.environ.get("LMSTUDIO_ENDPOINT", f"{LMSTUDIO_BASE_URL}/v1/completions")
GOEDEL_ENABLED = os.environ.get("GOEDEL_ENABLED", "true") != "false"  # Enable by default

# Cache LMStudio availability check for the session
lm_studio_available: bool | None = None
lm_studio_checked_at: int = 0
AVAILABILITY_CACHE_MS = 60000  # Re-check every 60s


opc_dir = os.environ.get("OPC_PROJECT_DIR")
STATE_DIR = str(Path(opc_dir) / ".opc" / "cache" / "lean") if opc_dir else str(Path(tempfile.gettempdir()) / "claude-lean")

STATE_FILE = str(Path(STATE_DIR) / "compiler-state.json")


def ensure_state_dir() -> None:
    Path(STATE_DIR).mkdir(parents=True, exist_ok=True)


def save_state(state: dict[str, Any]) -> None:
    ensure_state_dir()
    Path(STATE_FILE).write_text(json.dumps(state, indent=2), encoding="utf-8")


def run_lean_compiler(file_path: str, cwd: str) -> dict[str, Any]:
    # Add elan to PATH
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or ""
    elan_bin = str(Path(home) / ".elan" / "bin") if home else ""
    path_with_elan = elan_bin + os.pathsep + os.environ.get("PATH", "")

    try:
        # Try lake build first (for project files), or lean directly for standalone files
        cwd_path = Path(cwd)
        has_lakefile = (cwd_path / "lakefile.lean").exists() or (cwd_path / "lakefile.toml").exists()
        cmd = f'cd "{cwd}" && lake build 2>&1' if has_lakefile else f'lean "{file_path}" 2>&1'

        env = dict(os.environ)
        env["PATH"] = path_with_elan
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )

        output = result.stdout if result.stdout else result.stderr

        # Check for 'sorry' in the output or file
        sorries: list[str] = []
        file_path_obj = Path(file_path)
        file_content = file_path_obj.read_text(encoding="utf-8") if file_path_obj.exists() else ""
        if "sorry" in file_content:
            # Extract lines with sorry
            for i, line in enumerate(file_content.split("\n")):
                if "sorry" in line:
                    sorries.append(f"Line {i + 1}: {line.strip()}")

        if result.returncode != 0:
            return {"success": False, "output": output, "sorries": []}

        return {"success": True, "output": output, "sorries": sorries}
    except Exception:
        return {"success": False, "output": "", "sorries": []}


def extract_sorries(file_path: str) -> list[str]:
    file_path_obj = Path(file_path)
    if not file_path_obj.exists():
        return []

    content = file_path_obj.read_text(encoding="utf-8")
    sorries: list[str] = []
    for i, line in enumerate(content.split("\n")):
        if "sorry" in line:
            sorries.append(f"Line {i + 1}: {line.strip()}")

    return sorries


def check_lm_studio_available() -> bool:
    global lm_studio_available
    global lm_studio_checked_at

    # Return cached result if still valid
    now = int(time.time() * 1000)
    if lm_studio_available is not None and (now - lm_studio_checked_at) < AVAILABILITY_CACHE_MS:
        return lm_studio_available

    try:
        request = urllib.request.Request(f"{LMSTUDIO_BASE_URL}/v1/models", method="GET")
        with urllib.request.urlopen(request, timeout=2) as response:  # 2s timeout - fail fast
            lm_studio_available = response.status == 200
    except Exception:
        # Connection refused, timeout, or other network error
        lm_studio_available = False
    lm_studio_checked_at = now
    return lm_studio_available


def get_lm_studio_unavailable_message() -> str:
    return (
        f"\nℹ️ Godel-Prover not available (LMStudio not running at {LMSTUDIO_BASE_URL})\n"
        f"Lean compiler feedback only. To enable AI tactic suggestions:\n"
        f"1. Start LMStudio\n"
        f"2. Load goedel-prover-v2-8b model\n"
    )


def build_goedel_prompt(lean_code: str, errors: str, sorries: list[str]) -> str:
    if len(sorries) > 0:
        # Focus on fixing sorries with proof plan first (APOLLO pattern)
        return (
            f"Complete the following Lean 4 code:\n"
            f"\n"
            f"```lean4\n"
            f"{lean_code}\n"
            f"```\n"
            f"\n"
            f"The proof has {len(sorries)} incomplete part(s):\n"
            f"{chr(10).join(sorries)}\n"
            f"\n"
            f"Before producing the Lean 4 tactics to formally prove the given theorem, provide a detailed proof plan outlining the main proof steps and strategies.\n"
            f"The plan should highlight key ideas, intermediate lemmas, and proof structures that will guide the construction of the final formal proof.\n"
            f"\n"
            f"## Proof Plan\n"
            f"1. What is the goal?\n"
            f"2. What key lemmas or intermediate steps are needed?\n"
            f"3. What tactics will achieve each step?\n"
            f"\n"
            f"## Tactics\n"
            f"Provide the tactic(s) to replace the first sorry. Use tactics like: simp, ring, nlinarith, norm_num, exact, apply, rfl, ext, aesop_cat.\n"
            f"\n"
            f"Response:"
        )
    else:
        # Fix compiler errors
        return (
            f"Fix the following Lean 4 code that has compiler errors:\n"
            f"\n"
            f"```lean4\n"
            f"{lean_code}\n"
            f"```\n"
            f"\n"
            f"Compiler errors:\n"
            f"{errors[:1500]}\n"
            f"\n"
            f"Provide ONLY the corrected Lean 4 code or the specific fix needed.\n"
            f"\n"
            f"Fix:"
        )


def get_goedel_suggestions(lean_code: str, errors: str, sorries: list[str]) -> dict[str, Any]:
    if not GOEDEL_ENABLED:
        return {"suggestion": None, "unavailableMessage": None}

    # Check LMStudio availability first (fast, cached)
    if not check_lm_studio_available():
        return {"suggestion": None, "unavailableMessage": get_lm_studio_unavailable_message()}

    try:
        # Build prompt for Goedel prover
        prompt = build_goedel_prompt(lean_code, errors, sorries)

        body = json.dumps({
            "prompt": prompt,
            "max_tokens": 4096,
            "temperature": 0.6,
            "stop": ["```", "\n\n\n"],
        }).encode("utf-8")

        request = urllib.request.Request(
            LMSTUDIO_ENDPOINT,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:  # 30s timeout for actual inference
            data = json.loads(response.read().decode("utf-8"))

        choices = data.get("choices") or []
        suggestion = (choices[0].get("text") or "").strip() if choices else ""

        if not suggestion:
            return {"suggestion": None, "unavailableMessage": None}

        return {"suggestion": suggestion, "unavailableMessage": None}
    except Exception:
        # LMStudio error during inference - don't show unavailable message since health check passed
        return {"suggestion": None, "unavailableMessage": None}


def main() -> None:
    payload = read_stdin(timeout=2.0)

    # Only process Write tool on .lean files
    if tool_name(payload) != "Write":
        print("{}")
        return

    params = tool_input(payload)
    file_path = params.get("file_path") or params.get("filePath") or ""
    tool_response = payload.get("tool_response")
    if not file_path and isinstance(tool_response, dict):
        file_path = tool_response.get("filePath") or ""

    if not file_path.endswith(".lean"):
        print("{}")
        return

    cwd = os.environ.get("OPC_PROJECT_DIR") or (payload.get("cwd") if payload.get("cwd") else os.getcwd())

    # Run Lean compiler
    result = run_lean_compiler(file_path, cwd)
    sorries = extract_sorries(file_path)

    # Save state for Stop hook
    state: dict[str, Any] = {
        "session_id": payload.get("session_id", ""),
        "file_path": file_path,
        "has_errors": not result.get("success") or len(sorries) > 0,
        "errors": result.get("output", ""),
        "sorries": sorries,
        "timestamp": int(time.time() * 1000),
    }
    save_state(state)

    # Get Goedel suggestions if there are errors
    goedel_result: dict[str, Any] = {"suggestion": None, "unavailableMessage": None}
    if not result.get("success") or len(sorries) > 0:
        lean_code = ""
        if Path(file_path).exists():
            try:
                lean_code = Path(file_path).read_text(encoding="utf-8")
            except Exception:
                lean_code = ""
        goedel_result = get_goedel_suggestions(lean_code, result.get("output", ""), sorries)

    # Build Goedel suggestion block
    goedel_block = ""
    if goedel_result.get("suggestion"):
        goedel_block = f"\n🤖 GOEDEL-PROVER SUGGESTION:\n\n{goedel_result['suggestion']}\n"
    elif goedel_result.get("unavailableMessage"):
        goedel_block = goedel_result["unavailableMessage"]

    # Provide feedback to Claude
    if not result.get("success"):
        output: dict[str, Any] = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    f"\n⚠️ LEAN COMPILER ERRORS:\n"
                    f"\n"
                    f"{result.get('output', '')}\n"
                    f"{goedel_block}"
                    f"APOLLO Pattern: Use 'sorry' to mark failing sub-lemmas, then fix each one.\n"
                ),
            }
        }
        print(json.dumps(output))
    elif len(sorries) > 0:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    f"\n⚠️ LEAN PROOF INCOMPLETE - {len(sorries)} sorry placeholder(s):\n"
                    f"\n"
                    f"{chr(10).join(sorries)}\n"
                    f"{goedel_block}"
                    f"Fix each 'sorry' with a valid proof term or tactic.\n"
                ),
            }
        }
        print(json.dumps(output))
    else:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": "✓ Lean proof compiles successfully with no sorries!",
            }
        }
        print(json.dumps(output))


if __name__ == "__main__":
    main()
