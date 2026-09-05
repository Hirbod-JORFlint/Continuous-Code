#!/usr/bin/env python3
"""
Observe running agents and sessions - harness-neutral adapter.

USAGE:
    # Current session + transcript (driver-agnostic introspection)
    uv run python scripts/observe_agents.py --what session

    # Running agents (coordination store: PostgreSQL + JSONL registry)
    uv run python scripts/observe_agents.py --what agents
    uv run python scripts/observe_agents.py --what agents --session-id abc123

    # Sessions known to the coordination layer
    uv run python scripts/observe_agents.py --what sessions

    # Query PostgreSQL memory tables (DATABASE_URL)
    uv run python scripts/observe_agents.py --what memory --query "API"
    uv run python scripts/observe_agents.py --what memory --session-id abc123

    # Read blackboard HOT tier (JSON files under ~/.opc/blackboard)
    uv run python scripts/observe_agents.py --what blackboard

    # Coordination state (near-term activity: sessions + running agents)
    uv run python scripts/observe_agents.py --what tasks

    # List agent output files
    uv run python scripts/observe_agents.py --what outputs

    # Combined observation of all sources
    uv run python scripts/observe_agents.py --what all
    uv run python scripts/observe_agents.py --what all --session-id abc123 --query "error"

    # Output as JSON
    uv run python scripts/observe_agents.py --what all --json

This script provides unified observation of agent activity across:
1. PostgreSQL (archival_memory table) - persistent memory storage (DATABASE_URL)
2. Blackboard HOT tier (JSON files) - inter-agent communication
3. Coordination store (agents + sessions) - task coordination state
4. Agent outputs (markdown files under .opc/cache/agents/) - agent work products

Observation sources are harness-neutral: session/agent discovery goes through
`runtime.introspect` (driver registry) and `runtime.coordination` (PG + JSONL),
never through driver-specific command construction.
"""

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_OPC_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_OPC_SRC) in sys.path:
    sys.path.remove(str(_OPC_SRC))
sys.path.insert(0, str(_OPC_SRC))

from runtime import coordination, introspect  # noqa: E402

# Paths - use config-derived defaults (OPC_CONFIG_DIR canonical)
CONFIG_DIR = Path(os.environ.get("OPC_CONFIG_DIR") or str(Path.home() / ".opc"))
PROJECT_DIR = Path(os.environ.get("OPC_PROJECT_DIR") or os.getcwd())
BLACKBOARD_DIR = Path(os.environ.get("OPC_BLACKBOARD_DIR") or str(CONFIG_DIR / "blackboard"))
AGENTS_DIR = PROJECT_DIR / ".opc" / "cache" / "agents"
MEMORY_DB = CONFIG_DIR / "cache" / "memory.db"


async def query_memory(
    query: str | None = None, session_id: str | None = None, limit: int = 20
) -> list[dict]:
    """Query PostgreSQL memory tables (archival_memory via DATABASE_URL).

    Args:
        query: Text search query (searches content with ILIKE)
        session_id: Filter by session ID
        limit: Maximum number of results

    Returns:
        List of memory records as dictionaries
    """
    try:
        import asyncpg
    except ImportError:
        return query_memory_sqlite(query, session_id, limit)

    try:
        conn = await asyncpg.connect(
            os.environ.get(
                "DATABASE_URL",
                "postgresql://claude:claude_dev@localhost:5432/continuous_claude",
            ),
            timeout=3,
        )
        try:
            sql = """
                SELECT id, session_id, agent_id, content, created_at
                FROM archival_memory
                WHERE ($1::text IS NULL OR content ILIKE '%' || $1 || '%')
                AND ($2::text IS NULL OR session_id = $2)
                ORDER BY created_at DESC
                LIMIT $3
            """
            rows = await conn.fetch(sql, query, session_id, limit)
            return [dict(r) for r in rows]
        finally:
            await conn.close()
    except Exception:
        return query_memory_sqlite(query, session_id, limit)


def query_memory_sqlite(
    query: str | None = None, session_id: str | None = None, limit: int = 20
) -> list[dict]:
    """SQLite fallback for memory query (OPC_CONFIG_DIR/cache/memory.db)."""
    if not MEMORY_DB.exists():
        return []
    try:
        conn = sqlite3.connect(MEMORY_DB)
        conn.row_factory = sqlite3.Row
        try:
            sql = "SELECT * FROM archival_memory"
            where: list[str] = []
            params: list[Any] = []
            if query:
                where.append("content LIKE ?")
                params.append(f"%{query}%")
            if session_id:
                where.append("session_id = ?")
                params.append(session_id)
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()
    except Exception:
        return []


def current_session() -> dict:
    """Driver-agnostic current session via the driver registry."""
    return introspect.current_session()


def running_agents(session_id: str | None = None) -> list[dict]:
    """Running agents from the coordination store (PG + JSONL registry)."""
    return introspect.running_agents(session_id=session_id)


def list_sessions() -> list[dict]:
    """Sessions from the coordination layer."""
    return introspect.list_sessions()


def read_blackboard() -> dict[str, Any]:
    """Read all blackboard HOT tier JSON files."""
    results: dict[str, Any] = {}

    if not BLACKBOARD_DIR.exists():
        return results

    for f in sorted(BLACKBOARD_DIR.glob("*.json")):
        try:
            results[f.stem] = json.loads(f.read_text())
        except json.JSONDecodeError:
            results[f.stem] = {"error": "Invalid JSON"}
        except Exception as e:
            results[f.stem] = {"error": str(e)}

    return results


def coordination_state() -> dict[str, Any]:
    """Near-term activity: sessions + running agents + registry path."""
    return {
        "sessions": list_sessions(),
        "running_agents": running_agents(),
        "registry_path": str(coordination.registry_path()),
    }


def list_agent_outputs() -> list[dict]:
    """List agent output files.

    Returns:
        List of file metadata dictionaries, sorted by modification time (newest first)
    """
    results: list[dict] = []

    if not AGENTS_DIR.exists():
        return results

    for agent_dir in sorted(AGENTS_DIR.iterdir()):
        if agent_dir.is_dir():
            for f in sorted(agent_dir.glob("*.md")):
                try:
                    stat = f.stat()
                    results.append(
                        {
                            "agent": agent_dir.name,
                            "file": f.name,
                            "path": str(f),
                            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                            "size": stat.st_size,
                        }
                    )
                except Exception as e:
                    results.append(
                        {
                            "agent": agent_dir.name,
                            "file": f.name,
                            "path": str(f),
                            "error": str(e),
                        }
                    )

    return sorted(results, key=lambda x: x.get("modified", ""), reverse=True)


async def observe_all(query: str | None = None, session_id: str | None = None) -> dict[str, Any]:
    """Combined observation of all sources.

    Args:
        query: Text search query for memory
        session_id: Filter by session ID for memory and running agents

    Returns:
        Dictionary with all observation results and metadata
    """
    memory = await query_memory(query, session_id)
    blackboard = read_blackboard()
    coordination = coordination_state()
    outputs = list_agent_outputs()
    session = current_session()

    agent_items = running_agents(session_id)

    return {
        "timestamp": datetime.now().isoformat(),
        "filters": {
            "query": query,
            "session_id": session_id,
        },
        "session": session,
        "memory": {"count": len(memory), "items": memory},
        "blackboard": {"count": len(blackboard), "boards": blackboard},
        "coordination": {
            "count": len(agent_items),
            "sessions": coordination["sessions"],
            "agents": agent_items,
            "registry_path": coordination["registry_path"],
        },
        "outputs": {"count": len(outputs), "items": outputs},
    }


def format_memory_output(items: list[dict]) -> str:
    """Format memory items for human-readable output."""
    if not items:
        return "No memory records found."

    lines = ["=== Memory Records ===", ""]
    for item in items:
        lines.append(f"ID: {item.get('id', 'unknown')}")
        lines.append(f"Session: {item.get('session_id', 'unknown')}")
        lines.append(f"Agent: {item.get('agent_id', 'unknown')}")
        lines.append(f"Created: {item.get('created_at', 'unknown')}")
        content = item.get("content", "")
        # Truncate long content
        if len(content) > 200:
            content = content[:200] + "..."
        lines.append(f"Content: {content}")
        lines.append("-" * 40)
    return "\n".join(lines)


def format_current_session(session: dict) -> str:
    """Format the current session info."""
    if not session or not session.get("driver"):
        return "No harness driver installed / no current session."

    lines = ["=== Current Session ===", ""]
    lines.append(f"Driver: {session.get('driver', 'unknown')}")
    lines.append(f"Session ID: {session.get('session_id') or 'unknown'}")
    lines.append(f"Transcript: {session.get('transcript_path') or 'not found'}")
    return "\n".join(lines)


def format_agents_output(items: list[dict]) -> str:
    """Format running-agent items for human-readable output."""
    if not items:
        return "No running agents found."

    lines = ["=== Running Agents ===", ""]
    for item in items:
        lines.append(f"Agent: {item.get('agent_id', 'unknown')}")
        lines.append(f"Session: {item.get('session_id', 'unknown')}")
        lines.append(f"Status: {item.get('status', 'unknown')}")
        lines.append(f"Pattern: {item.get('pattern') or 'none'}")
        lines.append(f"DB: {item.get('db', 'jsonl')}")
        lines.append(f"Spawned: {item.get('spawned_at') or item.get('timestamp') or 'unknown'}")
        lines.append("-" * 40)
    return "\n".join(lines)


def format_sessions_output(items: list[dict]) -> str:
    """Format session items for human-readable output."""
    if not items:
        return "No sessions found in the coordination layer."

    lines = ["=== Sessions ===", ""]
    for item in items:
        lines.append(f"ID: {item.get('id', 'unknown')}")
        lines.append(f"Project: {item.get('project', 'unknown')}")
        lines.append(f"Working on: {item.get('working_on') or 'working...'}")
        lines.append(f"Heartbeat: {item.get('last_heartbeat', 'unknown')}")
        if item.get("agent_count") is not None:
            lines.append(f"Agents: {item.get('agent_count')}")
        lines.append("-" * 40)
    return "\n".join(lines)


def format_blackboard_output(boards: dict[str, Any]) -> str:
    """Format blackboard contents for human-readable output."""
    if not boards:
        return "No blackboard files found."

    lines = ["=== Blackboard HOT Tier ===", ""]
    for name, content in boards.items():
        lines.append(f"Board: {name}")
        if isinstance(content, dict) and "error" in content:
            lines.append(f"  Error: {content['error']}")
        else:
            # Show summary of content
            if isinstance(content, dict):
                lines.append(f"  Keys: {list(content.keys())}")
            elif isinstance(content, list):
                lines.append(f"  Items: {len(content)}")
            else:
                lines.append(f"  Type: {type(content).__name__}")
        lines.append("-" * 40)
    return "\n".join(lines)


def format_coordination_output(state: dict[str, Any]) -> str:
    """Format coordination state (near-term activity) for human-readable output."""
    lines = ["=== Coordination State ===", ""]
    lines.append(f"Sessions: {len(state.get('sessions', []))}")
    lines.append(f"Running Agents: {len(state.get('running_agents', []))}")
    lines.append(f"Registry: {state.get('registry_path', 'unknown')}")
    lines.append("")
    lines.append(format_sessions_output(state.get("sessions", [])))
    lines.append("")
    lines.append(format_agents_output(state.get("running_agents", [])))
    return "\n".join(lines)


def format_outputs_output(items: list[dict]) -> str:
    """Format output files for human-readable output."""
    if not items:
        return "No agent output files found."

    lines = ["=== Agent Outputs ===", ""]
    for item in items:
        lines.append(f"Agent: {item.get('agent', 'unknown')}")
        lines.append(f"File: {item.get('file', 'unknown')}")
        lines.append(f"Path: {item.get('path', 'unknown')}")
        lines.append(f"Modified: {item.get('modified', 'unknown')}")
        size = item.get("size", 0)
        if size > 1024:
            lines.append(f"Size: {size / 1024:.1f} KB")
        else:
            lines.append(f"Size: {size} bytes")
        lines.append("-" * 40)
    return "\n".join(lines)


def format_all_output(result: dict[str, Any]) -> str:
    """Format combined observation for human-readable output."""
    coordination = result.get("coordination", {})
    lines = [
        "=" * 60,
        "AGENT OBSERVATION REPORT",
        f"Timestamp: {result['timestamp']}",
        f"Filters: query={result['filters']['query']}, "
        f"session_id={result['filters']['session_id']}",
        "=" * 60,
        "",
        format_current_session(result.get("session", {})),
        "",
        f"Memory Records: {result['memory']['count']}",
        f"Blackboard Boards: {result['blackboard']['count']}",
        f"Sessions: {len(coordination.get('sessions', []))}",
        f"Running Agents: {len(coordination.get('agents', []))}",
        f"Output Files: {result['outputs']['count']}",
        "",
    ]

    if result["memory"]["items"]:
        lines.append(format_memory_output(result["memory"]["items"]))
        lines.append("")

    if result["blackboard"]["boards"]:
        lines.append(format_blackboard_output(result["blackboard"]["boards"]))
        lines.append("")

    if coordination.get("sessions") or coordination.get("agents"):
        lines.append(format_sessions_output(coordination.get("sessions", [])))
        lines.append("")
        lines.append(format_agents_output(coordination.get("agents", [])))
        lines.append("")

    if result["outputs"]["items"]:
        lines.append(format_outputs_output(result["outputs"]["items"]))

    return "\n".join(lines)


async def main():
    parser = argparse.ArgumentParser(
        description="Observe running agents - query memory, blackboard, sessions, and outputs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Current session + transcript (driver-agnostic)
  %(prog)s --what session

  # Running agents from the coordination store
  %(prog)s --what agents

  # Sessions known to the coordination layer
  %(prog)s --what sessions

  # Query memory for API-related content
  %(prog)s --what memory --query "API"

  # List tasks (coordination state: sessions + running agents)
  %(prog)s --what tasks

  # Combined observation with JSON output
  %(prog)s --what all --json
        """,
    )
    parser.add_argument(
        "--what",
        choices=["session", "agents", "sessions", "memory", "blackboard",
                 "tasks", "outputs", "all"],
        required=True,
        help=(
            "What to observe: session, agents, sessions, memory (PostgreSQL), "
            "blackboard (JSON files), tasks (coordination), outputs (markdown files), or all"
        ),
    )
    parser.add_argument(
        "--query", "-q", help="Search query for memory content (uses ILIKE pattern matching)"
    )
    parser.add_argument("--session-id", "-s", help="Filter by session ID (for memory and agents)")
    parser.add_argument(
        "--json", action="store_true", help="Output as JSON (default: human-readable format)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Limit number of results (for memory queries, default: 20)",
    )

    args = parser.parse_args()

    if args.what == "session":
        result = current_session()
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(format_current_session(result))

    elif args.what == "agents":
        result = running_agents(args.session_id)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(format_agents_output(result))

    elif args.what == "sessions":
        result = list_sessions()
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(format_sessions_output(result))

    elif args.what == "memory":
        result = await query_memory(args.query, args.session_id, args.limit)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(format_memory_output(result))

    elif args.what == "blackboard":
        result = read_blackboard()
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(format_blackboard_output(result))

    elif args.what == "tasks":
        result = coordination_state()
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(format_coordination_output(result))

    elif args.what == "outputs":
        result = list_agent_outputs()
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(format_outputs_output(result))

    elif args.what == "all":
        result = await observe_all(args.query, args.session_id)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(format_all_output(result))


if __name__ == "__main__":
    asyncio.run(main())
