#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""SessionStart Hook - Register session in coordination layer (Python port).

Port of .claude/hooks/src/session-register.ts:
  1. Registers the session in PostgreSQL for cross-session awareness
  2. Injects a system reminder about coordination layer features
  3. Shows other active sessions working on the same project

Part of the coordination layer architecture (Phase 1).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import session_id  # noqa: E402
from _lib import db_utils_pg  # noqa: E402
from _payload import read_stdin  # noqa: E402


def main() -> None:
    payload = read_stdin(timeout=2.0)

    sessionId = session_id.generate_session_id()
    project = session_id.get_project()
    projectName = project.split("/")[-1] or "unknown"

    # Store session ID in environment (same-process) and file (cross-process)
    os.environ["COORDINATION_SESSION_ID"] = sessionId
    if not session_id.write_session_id(sessionId):
        print(f"[session-register] WARNING: Failed to persist session ID {sessionId} to file", file=sys.stderr)

    # Register session in PostgreSQL
    db_utils_pg.register_session(sessionId, project, "")

    # Get other active sessions
    sessions_result = db_utils_pg.get_active_sessions(project)
    other_sessions = [s for s in sessions_result.get("sessions", []) if s.get("id") != sessionId]

    awareness = (
        f"<system-reminder>\n"
        f"MULTI-SESSION COORDINATION ACTIVE\n\n"
        f"Session: {sessionId}\n"
        f"Project: {projectName}\n"
    )

    if other_sessions:
        lines = "\n".join(
            f"  - {s.get('id', '?')}: {s.get('working_on') or 'working...'}" for s in other_sessions
        )
        awareness += (
            f"\nActive peer sessions ({len(other_sessions)}):\n{lines}\n\n"
            "Coordination features:\n"
            "- File edits are tracked to prevent conflicts\n"
            "- Research findings are shared automatically\n"
            "- Use Task tool normally - coordination happens via hooks\n"
        )
    else:
        awareness += (
            "\nNo other sessions active on this project.\n"
            "You are the only session currently working here.\n"
        )

    awareness += "</system-reminder>"

    output: dict[str, Any] = {"result": "continue", "message": awareness}
    print(json.dumps(output))


if __name__ == "__main__":
    main()
