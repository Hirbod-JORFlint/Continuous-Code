#!/usr/bin/env python3
"""Neutral hook payload adapter shared by harness lifecycle hooks (stdlib, 3.9+).

Reads the driver-specific stdin JSON payload once and exposes tolerant
accessors that abstract away Claude Code / Codex / opencode / Cline field
naming, with OPC_* environment fallbacks for drivers that enrich the env
instead of the payload (e.g. Codex's OPC_PROJECT_DIR).

Accessor policy: payload-first, environment fallback, generic default last.
Unknown payload shapes degrade to the same defaults, so hooks never crash on
a driver they have not been wired to yet.

Ships as `_payload.py` next to the hook scripts and is imported via a
sys.path shim so it never needs to be on PYTHONPATH or installed.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from typing import Any


def read_stdin(timeout: float = 2.0) -> dict[str, Any]:
    """Read a JSON object from stdin, or {} on timeout/parse failure.

    Uses a timed thread read so stdin never blocks forever on Windows
    (mirrors hook_launcher.read_stdin_with_timeout).
    """
    raw: str = ""
    try:
        holder: dict[str, str] = {}

        def _read() -> None:
            try:
                holder["data"] = sys.stdin.read()
            except Exception:
                holder["data"] = ""

        thread = threading.Thread(target=_read, daemon=True)
        thread.start()
        thread.join(timeout=timeout)
        raw = holder.get("data", "")
        if raw.strip():
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
    except Exception:
        pass
    return {}


def project_dir(payload: dict[str, Any] | None = None, default: str | None = None) -> str:
    """Resolve the project directory (payload first, then OPC_PROJECT_DIR)."""
    if isinstance(payload, dict):
        for key in ("cwd", "project_dir", "workspace_root"):
            value = payload.get(key)
            if value:
                return str(value)
    env = os.environ.get("OPC_PROJECT_DIR")
    if env:
        return env
    return default or os.getcwd()


def session_id(payload: dict[str, Any]) -> str:
    """Session identifier: payload session_id/sessionID, else env, else ppid."""
    sid = payload.get("session_id") or payload.get("sessionID")
    if not sid:
        sid = os.environ.get("OPC_SESSION_ID") or os.environ.get("OPC_PPID")
    return str(sid) if sid else "default"


def tool_name(payload: dict[str, Any]) -> str:
    """Tool name across payload shapes (tool_name, toolName)."""
    return str(payload.get("tool_name") or payload.get("toolName") or "")


def tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    """Tool parameters/arguments dictionary."""
    value = payload.get("tool_input") or payload.get("tool_input_params")
    return value if isinstance(value, dict) else {}


def prompt_text(payload: dict[str, Any]) -> str:
    """User prompt text across payload shapes."""
    prompt = payload.get("prompt")
    if not prompt:
        prompt = payload.get("prompt_submit")
    if not prompt:
        prompt = (payload.get("userPromptSubmit") or {}).get("message")
    if not prompt:
        prompt = ((payload.get("session") or {}).get("prompt"))
    return str(prompt) if prompt else ""


def exit_code(payload: dict[str, Any]) -> str:
    """Best-effort exit-code extraction across driver payload shapes."""
    for path in (("tool_output", "exit"), ("tool_response", "exit"),
                 ("tool_result", "exit"), ("tool_result", "exit_code")):
        value: Any = payload
        ok = True
        for key in path:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                ok = False
                break
        if ok and value is not None:
            return str(value)
    return "unknown"
