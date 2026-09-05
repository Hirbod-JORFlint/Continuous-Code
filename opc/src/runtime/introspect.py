"""Driver-agnostic session/agent introspection for continuity/event-observation adapters.

HARNESS-ADAPTER.md §10: adapters (observer, memory daemon, thinking extraction) discover the
current session, its transcript, and running agents through this module rather than constructing
driver-specific commands or reading driver-specific paths ad hoc.

Driver capabilities today:
- opencode: ``session list --format json`` for the current session id; `agent list` for
  config-defined agents; transcripts best-effort under the opencode storage root.
- codex: newest ``$CODEX_HOME/sessions/rollout-*.jsonl`` carries the current session id and IS the
  transcript.
- cline: no reliable CLI session/agent/transcript source (returns None // ()).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parent.parent
if str(_SRC) in sys.path:
    sys.path.remove(str(_SRC))
sys.path.insert(0, str(_SRC))

from harness.base import DriverRegistry, register_drivers  # noqa: E402

register_drivers()  # idempotent; ensures drivers are available to callers

from runtime import coordination as _coordination  # noqa: E402

AUTO_DRIVER_ORDER: tuple[str, ...] = ("opencode", "codex", "cline")


def resolve_driver(name: str | None = None):
    """Resolve a harness driver by name (``OPC_DRIVER``) or the first installed.

    Returns:
        The resolved HarnessDriver, or None if none is installed.
    """
    driver_name = (name or os.environ.get("OPC_DRIVER") or "auto").strip().lower() or "auto"
    try:
        if driver_name == "auto":
            return DriverRegistry.auto(AUTO_DRIVER_ORDER)
        return DriverRegistry.get(driver_name)
    except (KeyError, RuntimeError):
        return None


def _codex_transcript_path(home: str | None = None) -> Path | None:
    """Newest codex rollout JSONL under ``$CODEX_HOME/sessions``."""

    from harness.codex import _SESSION_FILE_RE

    root = Path(home or os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"))
    sessions_dir = root / "sessions"
    if not sessions_dir.is_dir():
        return None
    candidates: list[Path] = []
    for path in sessions_dir.rglob("rollout-*.jsonl"):
        if _SESSION_FILE_RE.match(path.name):
            candidates.append(path)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _opencode_storage_roots() -> list[Path]:
    """Candidate roots where opencode persists session transcripts."""
    home = Path.home()
    roots: list[Path] = []
    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        roots.append(Path(state_home) / "opencode")
    roots.append(home / ".local" / "share" / "opencode")
    if sys.platform == "win32":
        appdata = os.environ.get("LOCALAPPDATA")
        if appdata:
            roots.append(Path(appdata) / "opencode")
    return roots


def _opencode_transcript_path(session_id: str | None = None) -> Path | None:
    """Best-effort opencode transcript for ``session_id`` (None if not found)."""
    if not session_id:
        return None
    candidates: list[Path] = []
    for root in _opencode_storage_roots():
        if not root.is_dir():
            continue
        for path in root.rglob(f"{session_id}*"):
            if path.is_file() and path.suffix in (".jsonl", ".json"):
                candidates.append(path)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def resolve_transcript(driver=None, cwd: str | None = None) -> Path | None:
    """Locate the current session transcript for the resolved driver, or None.

    codex: the rollout JSONL; opencode: best-effort session storage probe; cline: None.
    """
    driver = driver or resolve_driver()
    if driver is None:
        return None
    if driver.name == "codex":
        return _codex_transcript_path()
    if driver.name == "opencode":
        session_id = driver.session_id(cwd)
        return _opencode_transcript_path(session_id)
    return None


def current_session(driver=None, cwd: str | None = None) -> dict[str, Any]:
    """Driver-agnostic current-session info for the resolved driver.

    Returns:
        dict with keys: driver, session_id, transcript_path (Path or None).
    """
    driver = driver or resolve_driver()
    if driver is None:
        return {"driver": None, "session_id": None, "transcript_path": None}
    session_id = driver.session_id(cwd)
    transcript = resolve_transcript(driver, cwd)
    return {
        "driver": driver.name,
        "session_id": session_id,
        "transcript_path": str(transcript) if transcript else None,
    }


def running_agents(driver=None, session_id: str | None = None) -> list[dict[str, Any]]:
    """Agents tracked by the coordination store (PostgreSQL + JSONL registry)."""
    return _coordination.running_agents(session_id)


def available_agents(driver=None) -> list[str]:
    """Config-defined agents from the harness driver ``list_agents()``."""
    driver = driver or resolve_driver()
    if driver is None:
        return []
    return list(driver.list_agents())


def list_sessions(driver=None, limit: int = 50) -> list[dict[str, Any]]:
    """Sessions from the coordination layer (PostgreSQL) plus registry entries."""
    sessions = _coordination.list_sessions(limit)
    if sessions:
        return sessions
    # Fallback: derive session ids from the JSONL registry.
    seen: dict[str, dict[str, Any]] = {}
    for rec in _coordination.read_registry():
        sid = rec.get("session_id")
        if sid and sid not in seen:
            seen[sid] = {
                "id": sid,
                "project": rec.get("project"),
                "working_on": rec.get("working_on"),
                "last_heartbeat": rec.get("spawned_at"),
                "agent_count": 1,
            }
    return list(seen.values())


def observe(driver=None, cwd: str | None = None) -> dict[str, Any]:
    """Unified observation view: session, transcripts, running agents, sessions."""
    driver = driver or resolve_driver()
    session = current_session(driver, cwd)
    driver_name = driver.name if driver is not None else None
    return {
        "driver": driver_name,
        "session": session,
        "running_agents": running_agents(driver_name),
        "available_agents": available_agents(driver),
        "sessions": list_sessions(driver_name),
        "registry_path": str(_coordination.registry_path()),
    }
