#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""PostToolUse Hook: Handoff Indexer (Python port).

Indexes handoff documents written to handoffs/. Injects braintrust root_span_id
into frontmatter when present, stores terminal session affinity, and triggers
artifact indexing.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _payload import read_stdin, tool_input, tool_name  # noqa: E402


def get_ppid(pid: int):
    """Get parent PID using ps command (Unix only)."""
    if sys.platform == "win32":
        # Windows: use wmic
        try:
            result = subprocess.run(
                ["wmic", "process", "where", f"ProcessId={pid}", "get", "ParentProcessId"],
                capture_output=True,
                text=True,
                timeout=5000,
            )
            for line in result.stdout.split("\n"):
                trimmed = line.strip()
                if trimmed.isdigit():
                    return int(trimmed)
        except Exception:
            # Ignore errors
            pass
        return None

    # Unix: use ps
    try:
        result = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5000,
        )
        ppid_str = result.stdout.strip()
        if ppid_str.isdigit():
            return int(ppid_str)
        return None
    except Exception:
        return None


def get_terminal_shell_pid():
    """Get terminal shell PID (great-grandparent).

    Process chain: Hook shell -> Claude -> Terminal shell
    """
    try:
        parent = os.getppid()  # Hook shell
        if not parent:
            return None
        grandparent = get_ppid(parent)  # Claude
        if not grandparent:
            return None
        return get_ppid(grandparent)  # Terminal shell
    except Exception:
        return None


def store_session_affinity(projectDir: str, terminalPid: int, sessionName: str) -> None:
    dbPath = Path(projectDir) / ".opc" / "cache" / "artifact-index" / "context.db"
    dbDir = dbPath.parent

    try:
        # Ensure directory exists
        dbDir.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(dbPath))

        # Create table if not exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS instance_sessions (
                terminal_pid TEXT PRIMARY KEY,
                session_name TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Insert or replace
        conn.execute(
            """
            INSERT OR REPLACE INTO instance_sessions (terminal_pid, session_name, updated_at)
            VALUES (?, ?, datetime('now'))
            """,
            (str(terminalPid), sessionName),
        )
        conn.commit()
        conn.close()
    except Exception:
        # Silently fail - don't block handoff creation
        pass


def extract_session_name(filePath: str):
    """Extract session name from handoff file path.

    Path format: .../handoffs/<session-name>/handoff-XXX.md
    """
    parts = filePath.replace("\\", "/").split("/")
    if "handoffs" in parts:
        handoffsIdx = parts.index("handoffs")
        if handoffsIdx >= 0 and handoffsIdx < len(parts) - 1:
            return parts[handoffsIdx + 1]
    return None


def main() -> None:
    payload = read_stdin(timeout=2.0)
    projectDir = os.environ.get("OPC_PROJECT_DIR") or os.getcwd()
    homeDir = os.environ.get("HOME") or os.environ.get("USERPROFILE") or ""

    # Only process Write tool calls
    if tool_name(payload) != "Write":
        print(json.dumps({"result": "continue"}))
        return

    params = tool_input(payload)
    filePath = params.get("file_path") or ""

    # Only process handoff files (.md or .yaml/.yml)
    isHandoffFile = filePath.endswith(".md") or filePath.endswith(".yaml") or filePath.endswith(".yml")
    if "handoffs" not in filePath or not isHandoffFile:
        print(json.dumps({"result": "continue"}))
        return

    try:
        fullPath = Path(filePath) if Path(filePath).is_absolute() else Path(projectDir) / filePath

        if not fullPath.exists():
            print(json.dumps({"result": "continue"}))
            return

        # Read current file content
        content = fullPath.read_text(encoding="utf-8")

        # Check if frontmatter already has root_span_id
        isYamlFile = fullPath.suffix in (".yaml", ".yml")
        hasFrontmatter = content.startswith("---")
        hasRootSpanId = "root_span_id:" in content

        # If missing root_span_id, try to inject it
        if not hasRootSpanId:
            # Read Braintrust state file
            sessionId = payload.get("session_id") or "default"
            stateFile = Path(homeDir) / ".opc" / "state" / "braintrust_sessions" / f"{sessionId}.json"

            if stateFile.exists():
                try:
                    state = json.loads(stateFile.read_text(encoding="utf-8"))

                    newFields = "\n".join([
                        f"root_span_id: {state.get('root_span_id')}",
                        f"turn_span_id: {state.get('current_turn_span_id') or ''}",
                        f"session_id: {sessionId}"
                    ])

                    if isYamlFile:
                        # For YAML files, prepend fields at the top (no frontmatter delimiters needed)
                        content = f"{newFields}\n{content}"
                    elif hasFrontmatter:
                        # Insert after opening ---
                        content = re.sub(r"^---\n", f"---\n{newFields}\n", content, count=1)
                    else:
                        # Add frontmatter at the start
                        content = f"---\n{newFields}\n---\n\n{content}"

                    # Write updated content atomically (temp file + rename)
                    tempPath = str(fullPath) + ".tmp"
                    with open(tempPath, "w", encoding="utf-8") as f:
                        f.write(content)
                    os.replace(tempPath, str(fullPath))
                except Exception:
                    # State file missing or invalid - continue without IDs
                    pass

        # Store session affinity: terminal_pid -> session_name
        terminalPid = get_terminal_shell_pid()
        sessionName = extract_session_name(str(fullPath))
        if terminalPid and sessionName:
            store_session_affinity(projectDir, terminalPid, sessionName)

        # Always trigger indexing (idempotent, will upsert)
        indexScript = Path(projectDir) / "scripts" / "artifact_index.py"

        if indexScript.exists():
            try:
                subprocess.Popen(
                    ["uv", "run", "python", str(indexScript), "--file", str(fullPath)],
                    cwd=projectDir,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    creationflags=subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0,
                )
            except Exception:
                pass

        print(json.dumps({"result": "continue"}))
    except Exception:
        # Don't block on errors
        print(json.dumps({"result": "continue"}))


if __name__ == "__main__":
    main()
