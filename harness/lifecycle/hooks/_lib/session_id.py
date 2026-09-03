#!/usr/bin/env python3
"""Session ID utilities for cross-process coordination (Python port).

Provides consistent session ID generation and persistence across hooks.
Session IDs are persisted to ~/.opc/.coordination-session-id to enable
cross-process sharing (each hook runs as a separate process).

Port of .claude/hooks/src/shared/session-id.ts, re-homed from the
Claude-only ~/.claude/.coordination-session-id to the neutral ~/.opc path.

Used by:
  - session-register.py (writes ID on session start)
  - file-claims.py (reads ID for file conflict detection)
"""

from __future__ import annotations

import os
import time
from pathlib import Path


def get_session_base_dir() -> Path:
    """Return the neutral config directory (~/.opc), creating it if needed."""
    base = Path.home() / ".opc"
    try:
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError:
        pass
    return base


def get_session_id_file(create_dir: bool = False) -> Path:
    """Path to the session ID persistence file under ~/.opc."""
    base = get_session_base_dir() if create_dir else Path.home() / ".opc"
    return base / ".coordination-session-id"


def generate_session_id() -> str:
    """Generate a new short session ID.

    Priority: BRAINTRUST_SPAN_ID (first 8 chars) > timestamp-based ID.

    Returns:
        8-char session identifier (e.g., "s-m1abc23")
    """
    span_id = os.environ.get("BRAINTRUST_SPAN_ID")
    if span_id:
        return span_id[:8]
    return f"s-{int(time.time() * 1000)}"


def write_session_id(session_id: str) -> bool:
    """Write the session ID to the persistence file.

    Returns:
        True if write succeeded, False otherwise
    """
    try:
        file_path = get_session_id_file(create_dir=True)
        file_path.write_text(session_id, encoding="utf-8")
        try:
            os.chmod(file_path, 0o600)
        except OSError:
            pass
        return True
    except OSError:
        return False


def read_session_id() -> str | None:
    """Read the session ID from the persistence file.

    Returns:
        The session ID if found, None otherwise
    """
    try:
        file_path = get_session_id_file()
        if not file_path.exists():
            return None
        value = file_path.read_text(encoding="utf-8").strip()
        return value or None
    except OSError:
        return None


def get_session_id(debug: bool = False) -> str:
    """Retrieve the session ID for coordination.

    Priority: COORDINATION_SESSION_ID env > file > BRAINTRUST_SPAN_ID > generated.

    Returns:
        Session identifier string
    """
    env_id = os.environ.get("COORDINATION_SESSION_ID")
    if env_id:
        return env_id

    file_id = read_session_id()
    if file_id:
        return file_id

    if debug:
        import sys
        print("[session-id] WARNING: No persisted session ID found, generating new one", file=sys.stderr)

    return generate_session_id()


def get_project() -> str:
    """Return the current project directory path.

    Returns:
        OPC_PROJECT_DIR env var or current working directory
    """
    return os.environ.get("OPC_PROJECT_DIR") or os.getcwd()
