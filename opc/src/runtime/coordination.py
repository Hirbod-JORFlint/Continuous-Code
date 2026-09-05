"""Neutral agent coordination store (harness-neutral).

Replaces the deleted ``scripts.agentica_patterns.coordination_pg`` module.
Coordinates spawned agents in PostgreSQL (same ``DATABASE_URL`` target as the
harness hooks' ``_lib/db_utils_pg.py``) with an append-only JSONL fallback under
the OPC runtime dir. Application code uses the sync helpers; the async
``CoordinationDB`` class is available for callers that already manage an event
loop.

Every PostgreSQL call degrades gracefully (returns ``False``/``[]``) so a
missing driver, network failure, or unreachable database never breaks
orchestration or observation.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_PG_URL = "postgresql://claude:claude_dev@localhost:5432/continuous_claude"

AGENTS_DDL = """
    CREATE TABLE IF NOT EXISTS agents (
        agent_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        pid INTEGER,
        parent_agent_id TEXT,
        pattern TEXT,
        premise TEXT,
        depth_level INTEGER DEFAULT 1,
        swarm_id TEXT,
        driver TEXT,
        status TEXT DEFAULT 'running',
        spawned_at TIMESTAMPTZ DEFAULT NOW(),
        completed_at TIMESTAMPTZ,
        result_summary TEXT
    )
"""


def pg_url() -> str:
    """Return the PostgreSQL connection string (canonical DATABASE_URL first)."""
    return os.environ.get("DATABASE_URL") or DEFAULT_PG_URL


def _opc_runtime_dir() -> Path:
    """Scratch/output root for spawned agents (mirrors spawn._runtime_dir)."""
    import tempfile

    return Path(os.environ.get("OPC_RUNTIME_DIR") or (Path(tempfile.gettempdir()) / "opc-agents"))


def registry_path(runtime_dir: Path | None = None) -> Path:
    """Path to the append-only JSONL agent registry."""
    return (runtime_dir or _opc_runtime_dir()) / "registry.jsonl"


def read_registry(runtime_dir: Path | None = None) -> list[dict[str, Any]]:
    """Read all records from the JSONL fallback registry (append-only)."""
    path = registry_path(runtime_dir)
    records: list[dict[str, Any]] = []
    if not path.is_file():
        return records
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return records


def write_registry_record(record: dict[str, Any]) -> None:
    """Append one record to the JSONL fallback registry."""
    path = registry_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass


class CoordinationDB:
    """Async PostgreSQL coordination store for spawned agents.

    Usable as an async context manager (``async with CoordinationDB() as db``).
    All methods swallow connection/query errors and return ``False``/``[]`` so
    callers never need try/except around database access.
    """

    def __init__(self, url: str | None = None) -> None:
        self._url = url or pg_url()
        self._conn = None

    async def __aenter__(self) -> CoordinationDB:
        await self._connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def _connect(self) -> None:
        if self._conn is not None:
            return
        try:
            import asyncpg

            self._conn = await asyncpg.connect(self._url, timeout=3)
            await self._conn.execute(AGENTS_DDL)
        except Exception:
            self._conn = None

    async def close(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None

    @property
    def available(self) -> bool:
        return self._conn is not None

    async def register_agent(
        self,
        *,
        agent_id: str,
        session_id: str,
        pid: int | None = None,
        parent_agent_id: str | None = None,
        pattern: str | None = None,
        premise: str | None = None,
        depth_level: int = 1,
        swarm_id: str | None = None,
        driver: str | None = None,
    ) -> bool:
        if self._conn is None:
            return False
        try:
            await self._conn.execute(
                """
                INSERT INTO agents (
                    agent_id, session_id, pid, parent_agent_id, pattern,
                    premise, depth_level, swarm_id, driver, status, spawned_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
                ON CONFLICT (agent_id) DO UPDATE SET
                    session_id = EXCLUDED.session_id,
                    pid = EXCLUDED.pid,
                    parent_agent_id = EXCLUDED.parent_agent_id,
                    pattern = EXCLUDED.pattern,
                    premise = EXCLUDED.premise,
                    depth_level = EXCLUDED.depth_level,
                    swarm_id = EXCLUDED.swarm_id,
                    driver = EXCLUDED.driver,
                    status = EXCLUDED.status
                """,
                agent_id,
                session_id,
                pid,
                parent_agent_id,
                pattern,
                premise,
                depth_level,
                swarm_id,
                driver,
                "running",
            )
            return True
        except Exception:
            return False

    async def update_agent_status(
        self,
        *,
        agent_id: str,
        status: str,
        result_summary: str | None = None,
    ) -> bool:
        if self._conn is None:
            return False
        try:
            await self._conn.execute(
                """
                UPDATE agents
                SET status = $2, result_summary = $3, completed_at = NOW()
                WHERE agent_id = $1
                """,
                agent_id,
                status,
                result_summary,
            )
            return True
        except Exception:
            return False

    async def list_running_agents(self, session_id: str | None = None) -> list[dict[str, Any]]:
        if self._conn is None:
            return []
        try:
            if session_id:
                rows = await self._conn.fetch(
                    """
                    SELECT * FROM agents
                    WHERE session_id = $1 AND status = 'running'
                    ORDER BY spawned_at DESC
                    """,
                    session_id,
                )
            else:
                rows = await self._conn.fetch(
                    "SELECT * FROM agents WHERE status = 'running' ORDER BY spawned_at DESC"
                )
            return [dict(r) for r in rows]
        except Exception:
            return []

    async def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        if self._conn is None:
            return []
        try:
            rows = await self._conn.fetch(
                """
                SELECT s.*, COUNT(a.agent_id) AS agent_count
                FROM sessions s
                LEFT JOIN agents a ON a.session_id = s.id
                GROUP BY s.id
                ORDER BY s.last_heartbeat DESC
                LIMIT $1
                """,
                limit,
            )
            return [dict(r) for r in rows]
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Sync helpers (fire-and-forget wrappers that never raise)
# ---------------------------------------------------------------------------

_lock = threading.Lock()


def _run_coro(coro):
    """Run an async helper regardless of the calling context.

    `asyncio.run()` cannot be invoked from inside a running event loop (never
    awaits the coroutine and raises), and Python 3.12+ forbids running a second
    loop in the same thread. When a loop is already running (e.g. the observer's
    async main), drive a throwaway loop on a dedicated daemon thread instead.
    """
    import asyncio
    import threading

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: list[object] = []
    error: list[BaseException] = []

    def _thread() -> None:
        loop = asyncio.new_event_loop()
        try:
            result.append(loop.run_until_complete(coro))
        except BaseException as exc:  # noqa: BLE001 - re-raised on caller thread
            error.append(exc)
        finally:
            loop.close()

    thread = threading.Thread(target=_thread, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0]


def register_agent(
    *,
    agent_id: str,
    session_id: str,
    pid: int | None = None,
    parent_agent_id: str | None = None,
    pattern: str | None = None,
    premise: str | None = None,
    depth_level: int = 1,
    swarm_id: str | None = None,
    driver: str | None = None,
) -> bool:
    """Register a spawned agent in PostgreSQL.

    Returns:
        True if the PostgreSQL write committed, False if unavailable.
    """
    try:
        async def _register() -> bool:
            async with CoordinationDB() as db:
                return await db.register_agent(
                    agent_id=agent_id,
                    session_id=session_id,
                    pid=pid,
                    parent_agent_id=parent_agent_id,
                    pattern=pattern,
                    premise=premise,
                    depth_level=depth_level,
                    swarm_id=swarm_id,
                    driver=driver,
                )

        with _lock:
            return _run_coro(_register())
    except Exception:
        return False


def update_agent_status(agent_id: str, status: str, result_summary: str | None = None) -> bool:
    """Record an agent's completion status in PostgreSQL."""
    try:
        async def _update() -> bool:
            async with CoordinationDB() as db:
                return await db.update_agent_status(
                    agent_id=agent_id, status=status, result_summary=result_summary
                )

        with _lock:
            return _run_coro(_update())
    except Exception:
        return False


def running_agents(session_id: str | None = None) -> list[dict[str, Any]]:
    """Running agents from the coordination store (PostgreSQL + JSONL fallback)."""
    agents: dict[str, dict[str, Any]] = {}
    for rec in read_registry():
        if rec.get("event") == "register":
            agents[str(rec.get("agent_id", ""))] = {
                **{k: v for k, v in rec.items() if k != "event"},
                "db": rec.get("db", "jsonl"),
            }
    completed: set[str] = {
        str(rec.get("agent_id", ""))
        for rec in read_registry()
        if rec.get("event") == "complete"
    }
    try:
        async def _list() -> list[dict[str, Any]]:
            async with CoordinationDB() as db:
                return await db.list_running_agents(session_id)

        with _lock:
            pg_rows = _run_coro(_list())
    except Exception:
        pg_rows = []
    for row in pg_rows:
        row["db"] = "postgres"
        agents.setdefault(str(row.get("agent_id", "")), row)

    for agent_id in completed:
        agents.pop(agent_id, None)

    if session_id:
        agents = {k: v for k, v in agents.items() if v.get("session_id") == session_id}
    return list(agents.values())


def list_sessions(limit: int = 50) -> list[dict[str, Any]]:
    """If the PostgreSQL coordination layer is reachable return its sessions."""
    try:
        async def _list() -> list[dict[str, Any]]:
            async with CoordinationDB() as db:
                return await db.list_sessions(limit)

        with _lock:
            return _run_coro(_list())
    except Exception:
        return []


def now_iso() -> str:
    """Current UTC timestamp in ISO format (for registry records)."""
    return datetime.now(UTC).isoformat()
