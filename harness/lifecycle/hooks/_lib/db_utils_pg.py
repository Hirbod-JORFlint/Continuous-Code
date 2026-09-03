#!/usr/bin/env python3
"""PostgreSQL coordination layer for harness lifecycle hooks (Python port).

Port of .claude/hooks/src/shared/db-utils-pg.ts. The TS implementation shells
out to `uv run python -c <asyncpg code>` from the opc directory (where asyncpg
and psycopg2 are project dependencies) with DATABASE_URL set. This Python port
mirrors that mechanism exactly so the DB behaviour is preserved byte-for-byte
and the hook process itself never needs to install asyncpg.

Resolution of the opc directory is re-homed from the Claude-only
`requireOpcDir()` (OPC_ROOT / OPC_PROJECT_DIR/opc / ~/.claude) to a neutral
`<project_dir>/opc` where project_dir = OPC_PROJECT_DIR (falls back to cwd).

Exposed (used by Phase C hooks):
  - register_session(session_id, project, working_on)
  - get_active_sessions(project)
  - check_file_claim(file_path, project, my_session_id)
  - claim_file(file_path, project, session_id)
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

DEFAULT_PG_URL = "postgresql://claude:claude_dev@localhost:5432/continuous_claude"


def get_pg_connection_string() -> str:
    """Return the PostgreSQL connection string.

    Priority: DATABASE_URL (canonical) > default local development connection.
    """
    return os.environ.get("DATABASE_URL") or DEFAULT_PG_URL


def _opc_dir() -> Path | None:
    """Resolve the opc directory (contains opc/pyproject.toml with asyncpg).

    Neutral re-home of the TS requireOpcDir(). Returns None if no project
    directory can be determined, in which case callers degrade gracefully.
    """
    project_dir = os.environ.get("OPC_PROJECT_DIR") or os.getcwd()
    candidate = Path(project_dir) / "opc"
    return candidate if candidate.is_dir() else None


def run_pg_query(python_code: str, args: list[str] | None = None) -> dict[str, Any]:
    """Execute async Python via a `uv run` subprocess using the opc env.

    Mirrors the TS runPgQuery(): spawns `uv run python -c <wrapped>` from the
    opc directory with DATABASE_URL set and a 5s timeout. The wrapped code
    receives args via sys.argv.

    Returns:
        dict with keys: success, stdout, stderr
    """
    opc_dir = _opc_dir()
    if opc_dir is None:
        return {"success": False, "stdout": "", "stderr": "opc dir not found"}

    wrapped_code = (
        "import sys\n"
        "import os\n"
        "import asyncio\n"
        "import json\n"
        f"sys.path.insert(0, {str(opc_dir)!r})\n"
        f"os.chdir({str(opc_dir)!r})\n"
        "\n"
        + python_code
    )

    cmd = ["uv", "run", "python", "-c", wrapped_code] + (args or [])
    env = dict(os.environ)
    env["DATABASE_URL"] = get_pg_connection_string()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(opc_dir),
            env=env,
        )
        return {
            "success": result.returncode == 0,
            "stdout": (result.stdout or "").strip(),
            "stderr": result.stderr or "",
        }
    except (subprocess.TimeoutExpired, OSError) as err:
        return {"success": False, "stdout": "", "stderr": str(err)}


def register_session(session_id: str, project: str, working_on: str = "") -> dict[str, Any]:
    """Register / upsert a session in the coordination layer.

    Returns:
        dict with keys: success, error? (error only when failed)
    """
    python_code = """
import asyncpg
import os

session_id = sys.argv[1]
project = sys.argv[2]
working_on = sys.argv[3] if len(sys.argv) > 3 else ''
pg_url = os.environ.get('DATABASE_URL', 'postgresql://claude:claude_dev@localhost:5432/continuous_claude')

async def main():
    conn = await asyncpg.connect(pg_url)
    try:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                working_on TEXT,
                started_at TIMESTAMP DEFAULT NOW(),
                last_heartbeat TIMESTAMP DEFAULT NOW()
            )
        ''')
        await conn.execute('''
            INSERT INTO sessions (id, project, working_on, started_at, last_heartbeat)
            VALUES ($1, $2, $3, NOW(), NOW())
            ON CONFLICT (id) DO UPDATE SET
                working_on = EXCLUDED.working_on,
                last_heartbeat = NOW()
        ''', session_id, project, working_on)
        print('ok')
    finally:
        await conn.close()

asyncio.run(main())
"""
    result = run_pg_query(python_code, [session_id, project, working_on])
    if not result["success"] or result["stdout"] != "ok":
        return {
            "success": False,
            "error": result["stderr"] or result["stdout"] or "Unknown error",
        }
    return {"success": True}


def get_active_sessions(project: str | None = None) -> dict[str, Any]:
    """Get sessions active in the last 5 minutes from the coordination layer.

    Returns:
        dict with keys: success, sessions (list of dicts)
    """
    python_code = """
import asyncpg
import os
import json
from datetime import datetime, timedelta

project_filter = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] != 'null' else None
pg_url = os.environ.get('DATABASE_URL', 'postgresql://claude:claude_dev@localhost:5432/continuous_claude')

async def main():
    conn = await asyncpg.connect(pg_url)
    try:
        cutoff = datetime.utcnow() - timedelta(minutes=5)

        if project_filter:
            rows = await conn.fetch('''
                SELECT id, project, working_on, started_at, last_heartbeat
                FROM sessions
                WHERE project = $1 AND last_heartbeat > $2
                ORDER BY started_at DESC
            ''', project_filter, cutoff)
        else:
            rows = await conn.fetch('''
                SELECT id, project, working_on, started_at, last_heartbeat
                FROM sessions
                WHERE last_heartbeat > $1
                ORDER BY started_at DESC
            ''', cutoff)

        sessions = []
        for row in rows:
            sessions.append({
                'id': row['id'],
                'project': row['project'],
                'working_on': row['working_on'],
                'started_at': row['started_at'].isoformat() if row['started_at'] else None,
                'last_heartbeat': row['last_heartbeat'].isoformat() if row['last_heartbeat'] else None
            })

        print(json.dumps(sessions))
    except Exception:
        print(json.dumps([]))
    finally:
        await conn.close()

asyncio.run(main())
"""
    result = run_pg_query(python_code, [project or "null"])
    if not result["success"]:
        return {"success": False, "sessions": []}
    try:
        sessions = __import__("json").loads(result["stdout"] or "[]")
        return {"success": True, "sessions": sessions}
    except Exception:
        return {"success": False, "sessions": []}


def check_file_claim(file_path: str, project: str, my_session_id: str) -> dict[str, Any]:
    """Check if a file is claimed by another session.

    Returns:
        dict with keys: claimed, claimedBy?, claimedAt?
    """
    python_code = """
import asyncpg
import os
import json

file_path = sys.argv[1]
project = sys.argv[2]
my_session_id = sys.argv[3]
pg_url = os.environ.get('DATABASE_URL', 'postgresql://claude:claude_dev@localhost:5432/continuous_claude')

async def main():
    conn = await asyncpg.connect(pg_url)
    try:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS file_claims (
                file_path TEXT,
                project TEXT,
                session_id TEXT,
                claimed_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (file_path, project)
            )
        ''')
        row = await conn.fetchrow('''
            SELECT session_id, claimed_at FROM file_claims
            WHERE file_path = $1 AND project = $2 AND session_id != $3
        ''', file_path, project, my_session_id)

        if row:
            print(json.dumps({
                'claimed': True,
                'claimedBy': row['session_id'],
                'claimedAt': row['claimed_at'].isoformat() if row['claimed_at'] else None
            }))
        else:
            print(json.dumps({'claimed': False}))
    finally:
        await conn.close()

asyncio.run(main())
"""
    result = run_pg_query(python_code, [file_path, project, my_session_id])
    if not result["success"]:
        return {"claimed": False}
    try:
        return __import__("json").loads(result["stdout"] or '{"claimed": false}')
    except Exception:
        return {"claimed": False}


def claim_file(file_path: str, project: str, session_id: str) -> dict[str, Any]:
    """Claim a file for the current session.

    Returns:
        dict with key: success
    """
    python_code = """
import asyncpg
import os

file_path = sys.argv[1]
project = sys.argv[2]
session_id = sys.argv[3]
pg_url = os.environ.get('DATABASE_URL', 'postgresql://claude:claude_dev@localhost:5432/continuous_claude')

async def main():
    conn = await asyncpg.connect(pg_url)
    try:
        await conn.execute('''
            INSERT INTO file_claims (file_path, project, session_id, claimed_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (file_path, project) DO UPDATE SET
                session_id = EXCLUDED.session_id,
                claimed_at = NOW()
        ''', file_path, project, session_id)
        print('ok')
    finally:
        await conn.close()

asyncio.run(main())
"""
    result = run_pg_query(python_code, [file_path, project, session_id])
    return {"success": result["success"] and result["stdout"] == "ok"}
