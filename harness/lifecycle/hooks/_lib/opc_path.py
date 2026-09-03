#!/usr/bin/env python3
"""Cross-platform OPC directory resolution for harness hooks (Python port of
shared/opc-path.ts). Pure stdlib, driver-neutral.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["get_opc_dir", "require_opc_dir", "has_opc_dir"]


def get_opc_dir() -> str | None:
    """Resolve the OPC (Continuous-Code) directory, or None if unavailable.

    Resolution order:
      1. OPC_ROOT env var (global setup)
      2. ${OPC_PROJECT_DIR}/opc (local setup)
      3. ${CWD}/opc (fallback)
      4. ~/.opc (neutral global-install base, mirroring the old ~/.claude path)
    """
    env_opc = os.environ.get("OPC_ROOT")
    if env_opc and Path(env_opc).is_dir():
        return env_opc

    project_dir = os.environ.get("OPC_PROJECT_DIR") or os.getcwd()
    local_opc = Path(project_dir) / "opc"
    if local_opc.is_dir():
        return str(local_opc)

    home_dir = os.environ.get("HOME") or os.environ.get("USERPROFILE") or ""
    if home_dir:
        neutral_global = Path(home_dir) / ".opc"
        scripts_core = neutral_global / "scripts" / "core"
        if scripts_core.is_dir():
            return str(neutral_global)

    return None


def has_opc_dir() -> bool:
    """True if OPC infrastructure is available (optional features can skip)."""
    return get_opc_dir() is not None


def require_opc_dir() -> str:
    """Like get_opc_dir but exits gracefully (stdout continue) if unavailable."""
    opc_dir = get_opc_dir()
    if not opc_dir:
        print('{"result": "continue"}')
        raise SystemExit(0)
    return opc_dir
