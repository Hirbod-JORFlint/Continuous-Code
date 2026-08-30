"""Canonical environment variable names and accessors for Continuous Code.

All harnesses (Opencode, Codex, Cline) share one neutral contract surfaced
through these variables. Harness layers set them; the runtime reads them.

Env contract (see harness/spec/HARNESS-ADAPTER.md):
    OPC_ROOT          - install root of the Continuous Code runtime
    OPC_PROJECT_DIR   - project directory the session was started in
    OPC_SESSION_ID    - unique id for the current session
    OPC_ENV_FILE      - write-through env dump for the session
    OPC_PPID          - parent process id of the harness/agent process
    OPC_CONFIG_DIR    - harness-neutral config/state root (default ~/.opc)
    DATABASE_URL      - PostgreSQL connection string (canonical)
"""

import os
from pathlib import Path

OPC_ROOT = "OPC_ROOT"
OPC_PROJECT_DIR = "OPC_PROJECT_DIR"
OPC_SESSION_ID = "OPC_SESSION_ID"
OPC_ENV_FILE = "OPC_ENV_FILE"
OPC_PPID = "OPC_PPID"
OPC_CONFIG_DIR = "OPC_CONFIG_DIR"
DATABASE_URL = "DATABASE_URL"

_DEFAULT_CONFIG_DIR = Path.home() / ".opc"


def get_opc_root() -> str | None:
    """Install root of the Continuous Code runtime (OPC_ROOT env)."""
    return os.environ.get(OPC_ROOT)


def get_project_dir(default: Path | None = None) -> Path:
    """Project directory for the current session."""
    return Path(os.environ.get(OPC_PROJECT_DIR, str(default or Path.cwd())))


def get_session_id(default: str = "default") -> str:
    """Unique id for the current session."""
    return os.environ.get(OPC_SESSION_ID, default)


def get_config_dir() -> Path:
    """Harness-neutral config/state root. Respects OPC_CONFIG_DIR."""
    return Path(os.environ.get(OPC_CONFIG_DIR, str(_DEFAULT_CONFIG_DIR)))


def get_global_env_path() -> Path:
    """Global .env fallback written by the installer."""
    return get_config_dir() / ".env"


def get_global_mcp_config_path() -> Path:
    """Global MCP registry fallback written by the installer."""
    return get_config_dir() / "mcp_config.json"


def is_postgres_configured() -> bool:
    """True when DATABASE_URL is set."""
    return bool(os.environ.get(DATABASE_URL))