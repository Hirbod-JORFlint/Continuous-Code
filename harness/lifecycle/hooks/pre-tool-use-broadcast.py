#!/usr/bin/env python3
"""PreToolUse hook: Surface swarm broadcasts from the coordination DB.

Neutral Python port of pre-tool-use-broadcast.ts (Step 11, Phase B).
Re-homes the coordination DB from .claude/cache/... to .opc/cache/....

When running inside an agentica swarm (SWARM_ID set), queries the broadcasts
table and injects recent broadcasts from other agents as context.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

from _payload import read_stdin, project_dir

SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def main() -> None:
    read_stdin()

    swarm_id = os.environ.get("SWARM_ID")
    if not swarm_id:
        print(json.dumps({"result": "continue"}))
        return

    if not SAFE_ID_PATTERN.match(swarm_id):
        print(json.dumps({"result": "continue"}))
        return

    agent_id = os.environ.get("AGENT_ID", "unknown")
    if agent_id != "unknown" and not SAFE_ID_PATTERN.match(agent_id):
        print(json.dumps({"result": "continue"}))
        return

    project_root = Path(project_dir(None, default=str(Path.cwd())))
    db_path = project_root / ".opc" / "cache" / "agentica-coordination" / "coordination.db"
    if not db_path.exists():
        print(json.dumps({"result": "continue"}))
        return

    try:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            pass
        rows = conn.execute(
            """
            SELECT sender_agent, broadcast_type, payload, created_at
            FROM broadcasts
            WHERE swarm_id = ? AND sender_agent != ?
            ORDER BY created_at DESC
            LIMIT 10
            """,
            (swarm_id, agent_id),
        ).fetchall()
        conn.close()

        broadcasts = [
            {
                "sender": row["sender_agent"],
                "type": row["broadcast_type"],
                "payload": json.loads(row["payload"] or "null"),
                "time": row["created_at"],
            }
            for row in rows
        ]
    except (sqlite3.DatabaseError, ValueError):
        print(json.dumps({"result": "continue"}))
        return

    if broadcasts:
        lines = ["\n--- SWARM BROADCASTS ---\n"]
        for b in broadcasts:
            lines.append(f"[{b['type'].upper()}] from {b['sender']}:")
            lines.append(f"  {json.dumps(b['payload'])}")
        lines.append("------------------------\n")
        print(json.dumps({"result": "continue", "message": "\n".join(lines)}))
    else:
        print(json.dumps({"result": "continue"}))


if __name__ == "__main__":
    main()
